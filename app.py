# version 11c

import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse
import teams_markdown

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity
# *** Important: import TeamsInfo ***
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Thread-safe conversation state management
conversation_states = {}
state_lock = Lock()

def get_conversation_state(conversation_id):
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                'history': [],
                'cache': {},
                'last_activity': None
            }
        return conversation_states[conversation_id]

def cleanup_old_states():
    """Clean up conversation states older than 24 hours"""
    with state_lock:
        current_time = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state['last_activity'] and (current_time - state['last_activity']) > 86400:  # 24 hours
                del conversation_states[conv_id]

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body = request.json
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state['last_activity'] = asyncio.get_event_loop().time()
    
    # Clean up old states periodically
    if len(conversation_states) > 100:  # Only clean up if we have many states
        cleanup_old_states()

    # Set the conversation state for this request
    import ask_func
    ask_func.chat_history = state['history']
    ask_func.tool_cache = state['cache']

    user_message = turn_context.activity.text or ""

    # --------------------------------------------------------------------
    # Use 'TeamsInfo.get_member' to get userPrincipalName or email
    # --------------------------------------------------------------------
    user_id = "anonymous"  # fallback
    try:
        # 'from_property.id' usually holds the "29:..." Teams user ID
        teams_user_id = turn_context.activity.from_property.id

        # This call will attempt to fetch the user's profile from Teams
        teams_member = await TeamsInfo.get_member(turn_context, teams_user_id)
        # If successful, you can read these fields:
        #   teams_member.user_principal_name (often the email/UPN)
        #   teams_member.email
        #   teams_member.name
        #   teams_member.id
        if teams_member and teams_member.user_principal_name:
            user_id = teams_member.user_principal_name
        elif teams_member and teams_member.email:
            user_id = teams_member.email
        else:
            user_id = teams_user_id  # fallback if we can't get an email

    except Exception as e:
        # If get_member call fails (e.g., in a group chat scenario or permission issues),
        # just fallback to the "29:..." ID or 'anonymous'
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Show "thinking" indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    try:
        # Process the message
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # Parse and format the response
        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        match = re.search(source_pattern, answer_text, flags=re.DOTALL)

        # Normalize the LLM output into JSON
        cleaned = answer_text.strip()
        # strip ```json / ``` wrappers & escape newlines
        if cleaned.startswith('```json'):
            cleaned = cleaned[7:].strip()
        if cleaned.startswith('```'):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3].strip()
        cleaned = cleaned.replace('\n', '\\n')
        # Try to parse the response as JSON first
        try:
            response_json = json.loads(cleaned)
            
        except json.JSONDecodeError:
        # not structured JSON → fall through to Adaptive Card or plain text
           response_json = None  # signal “no JSON”
            # Check if this is our expected JSON format with content and source
        
        if isinstance(response_json, dict) and "content" in response_json and "source" in response_json:
            # We have a structured JSON response!
            content_items = response_json["content"]
            source = response_json["source"]
            files = response_json["source_details"].get("file_names", [])
            tables = response_json["source_details"].get("table_names", [])

            # Render Markdown fallback for Teams
            md = teams_markdown.render(
                question=user_message,
                content=content_items,
                source=source,
                files=files,
                tables=tables
            )
                
            await turn_context.send_activity(Activity(type="message", text=md))
            return
        

        # 2️⃣ If JSON but no Markdown sent above, build Adaptive Card
        if isinstance(response_json, dict) and "content" in response_json:
            content_items = response_json["content"]
            source = response_json.get("source", "")
            files = response_json["source_details"].get("file_names", [])
            tables = response_json["source_details"].get("table_names", [])

            
            # Build the adaptive card body
            body_blocks = []
            #referenced_paragraphs = []
            #calculated_paragraphs = []
            #other_paragraphs = []
            # Process each content item based on its type
            for item in content_items:
                item_type = item.get("type", "")
                if item_type == "heading":
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": item.get("text", ""),
                        "wrap": True,
                        "weight": "Bolder",
                        "size": "Large",
                        "spacing": "Medium"
                    })
                elif item_type == "paragraph":
                    text = item.get("text", "")
                    # Only add to main body if not a reference/calculated paragraph
                    if not (text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:")):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": text,
                            "wrap": True,
                            "spacing": "Small"
                        })
                elif item_type == "bullet_list":
                    items = item.get("items", [])
                    for list_item in items:
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"• {list_item}",
                            "wrap": True,
                            "spacing": "Small"
                        })
                elif item_type == "numbered_list":
                    items = item.get("items", [])
                    for i, list_item in enumerate(items, 1):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"{i}. {list_item}",
                            "wrap": True,
                            "spacing": "Small"
                        })
                elif item_type == "code_block":
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": f"```\n{item.get('code', '')}\n```",
                        "wrap": True,
                        "fontType": "Monospace",
                        "spacing": "Medium"
                    })
                # Add all non-source paragraphs to the main body
                # for text in other_paragraphs:
                #     body_blocks.append({
                #         "type": "TextBlock",
                #         "text": text,
                #         "wrap": True,
                #         "spacing": "Small"
                #     })
                # Create the source section
                source_container = {
                    "type": "Container",
                    "id": "sourceContainer",
                    "isVisible": False,
                    "style": "emphasis",
                    "bleed": True,
                    "maxHeight": "500px",
                    "isScrollable": True, 
                    "items": []
                }
                # Add Referenced/Calculated paragraphs to the collapsible section if present
                for item in content_items:
                    if item.get("type", "") == "paragraph":
                        text = item.get("text", "")
                        if text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:"):
                            lines = text.split("\n")
                            # Add the heading ("Referenced:" or "Calculated using:")
                            if lines:
                                source_container["items"].append({
                                    "type": "TextBlock",
                                    "text": lines[0],
                                    "wrap": True,
                                    "spacing": "Small",
                                    "weight": "Bolder"
                                })
                            # For each file/table, add a markdown link as a TextBlock
                            for line in lines[1:]:
                                if line.strip().startswith("-"):
                                    fname = line.strip()[1:].strip()
                                    if fname:
                                        sharepoint_base = "https://dgda.sharepoint.com/:x:/r/sites/CXQAData/_layouts/15/Doc.aspx?sourcedoc=%7B9B3CA3CD-5044-45C7-8A82-0604A1675F46%7D&file={}&action=default&mobileredirect=true"
                                        url = sharepoint_base.format(urllib.parse.quote(fname))
                                        print(f"DEBUG: Adding file link: {fname} -> {url}")
                                        source_container["items"].append({
                                            "type": "TextBlock",
                                            "text": f"[{fname}]({url})",
                                            "wrap": True,
                                            "spacing": "Small"
                                        })
                                else:
                                    # If not a file line, just add as text
                                    source_container["items"].append({
                                        "type": "TextBlock",
                                        "text": line,
                                        "wrap": True,
                                        "spacing": "Small"
                                    })
                # Remove file_names/table_names and code/file blocks from the collapsible section
                # Always add the source line at the bottom of the container
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": f"Source: {source}",
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Accent",
                    "spacing": "Medium",
                })
                body_blocks.append(source_container)
                # Add the show/hide source buttons
                body_blocks.append({
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "id": "showSourceBtn",
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Show Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        },
                        {
                            "type": "Column",
                            "id": "hideSourceBtn",
                            "isVisible": False,
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Hide Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                })
                # Create and send the adaptive card
                adaptive_card = {
                    "type": "AdaptiveCard",
                    "body": body_blocks,
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.5"
                }
                message = Activity(
                    type="message",
                    attachments=[{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": adaptive_card
                    }]
                )
                
            
        # If we're here, the response wasn't valid JSON, so process normally
        if match:
            main_answer = match.group(1).strip()
            source_line = match.group(2).strip()
            appended_details = match.group(3) if match.group(3) else ""
        else:
            main_answer = answer_text
            source_line = ""
            appended_details = ""

        if source_line:
            # Create simple text blocks without complex formatting
            body_blocks = [{
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            }]
            
            # Create the collapsible source container
            if source_line or appended_details:
                # Create a container that will be toggled
                source_container = {
                    "type": "Container",
                    "id": "sourceContainer",
                    "isVisible": False,
                    "style": "emphasis",
                    "bleed": True,
                    "maxHeight": "500px",
                    "isScrollable": True, 
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": source_line,
                            "wrap": True,
                            "weight": "Bolder",
                            "color": "Accent",
                            "spacing": "Medium",
                        }
                    ]
                }
                
                # Add source details if it exists
                if appended_details:
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": appended_details.strip(),
                        "wrap": True,
                        "spacing": "Small"
                    })
                    
                body_blocks.append(source_container)
                
                body_blocks.append({
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "id": "showSourceBtn",
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Show Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        },
                        {
                            "type": "Column",
                            "id": "hideSourceBtn",
                            "isVisible": False,
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Hide Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                })


            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }
            
            message = Activity(
                type="message",
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card
                }]
            )
            await turn_context.send_activity(message)
        else:
            # For simple responses without source, send formatted markdown directly
            # Teams supports some markdown in regular messages
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
