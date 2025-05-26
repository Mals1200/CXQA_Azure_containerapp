# version 11c
# Made it display the files sources for the compounded questions:
    # Referenced: <Files>     
    # Calculated using: <Tables>

import os
import asyncio
from threading import Lock
import re
import json

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
        answer_chunks = []
        try:
            for chunk in Ask_Question(user_message, user_id=user_id):
                answer_chunks.append(chunk)
            answer_text = "".join(answer_chunks)
        except Exception as e:
            answer_text = f"Sorry, an error occurred while answering your question: {e}"

        if not answer_text.strip():
            answer_text = "Sorry, I couldn't find an answer or something went wrong."

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # Parse and format the response
        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        match = re.search(source_pattern, answer_text, flags=re.DOTALL)

        # Try to parse the response as JSON first
        try:
            # Remove code block markers if present
            cleaned_answer_text = answer_text.strip()
            if cleaned_answer_text.startswith('```json'):
                cleaned_answer_text = cleaned_answer_text[7:].strip()
            if cleaned_answer_text.startswith('```'):
                cleaned_answer_text = cleaned_answer_text[3:].strip()
            if cleaned_answer_text.endswith('```'):
                cleaned_answer_text = cleaned_answer_text[:-3].strip()
            # DEBUG: Print the cleaned answer text before JSON parsing
            print("[DEBUG] Cleaned answer text for JSON parsing:", cleaned_answer_text)
            # DO NOT replace '\n' with '\\n' here (removing that step)
            response_json = json.loads(cleaned_answer_text)
            # Check if this is our expected JSON format with content and source
            if isinstance(response_json, dict) and "content" in response_json and "source" in response_json:
                # We have a structured JSON response!
                content_items = response_json["content"]
                source = response_json["source"]
                # Build the adaptive card body
                body_blocks = []
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
                # Add Referenced/Calculated paragraphs to the collapsible section if present
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
                for item in content_items:
                    if item.get("type", "") == "paragraph":
                        text = item.get("text", "")
                        if text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:"):
                            source_container["items"].append({
                                "type": "TextBlock",
                                "text": text,
                                "wrap": True,
                                "spacing": "Small"
                            })
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": f"Source: {source}",
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Accent",
                    "spacing": "Medium",
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
                # DEBUG: Print the outgoing Adaptive Card JSON
                print("[DEBUG] Outgoing Adaptive Card JSON:", json.dumps(adaptive_card, indent=2))
                message = Activity(
                    type="message",
                    attachments=[{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": adaptive_card
                    }]
                )
                await turn_context.send_activity(message)
                # Successfully processed JSON, so return early
                return
                
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # Not JSON or not in our expected format, fall back to the regular processing
            print(f"[DEBUG] JSON parsing failed: {e}")
            pass
            
        # If we're here, the response wasn't valid JSON, so process normally
        if match:
            main_answer = match.group(1).strip()
            source_line = match.group(2).strip()
            appended_details = match.group(3) if match.group(3) else ""
        else:
            main_answer = answer_text
            source_line = ""
            appended_details = ""

        # DEBUG: Print the plain text answer before sending
        print("[DEBUG] Plain text answer:", main_answer)

        if source_line:
            # Create simple text blocks without complex formatting
            body_blocks = [{
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            }]
            
            # Create the collapsible source container
            if source_line or appended_details:
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
            # DEBUG: Print the outgoing Adaptive Card JSON
            print("[DEBUG] Outgoing Adaptive Card JSON:", json.dumps(adaptive_card, indent=2))
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
            print("[DEBUG] Sending fallback plain text message:", main_answer)
            await turn_context.send_activity(Activity(type="message", text=main_answer))

        # --- CATCH-ALL FALLBACK: If nothing was sent, send a minimal message ---
        # (This should never be reached, but just in case)
        # await turn_context.send_activity(Activity(type="message", text="Sorry, I couldn't process your answer."))

        # --- MINIMAL CARD TEST BLOCK (Uncomment to test minimal card rendering) ---
        # minimal_card = {
        #     "type": "AdaptiveCard",
        #     "body": [
        #         {
        #             "type": "TextBlock",
        #             "text": "Test message from bot.",
        #             "wrap": True
        #         }
        #     ],
        #     "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        #     "version": "1.5"
        # }
        # print("[DEBUG] Outgoing Minimal Adaptive Card JSON:", json.dumps(minimal_card, indent=2))
        # message = Activity(
        #     type="message",
        #     attachments=[{
        #         "contentType": "application/vnd.microsoft.card.adaptive",
        #         "content": minimal_card
        #     }]
        # )
        # await turn_context.send_activity(message)

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
