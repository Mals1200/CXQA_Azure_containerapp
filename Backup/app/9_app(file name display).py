# Version 9:
# fixed the filename variables in ask_func.py verion 19b.
# This app.py version takes that variable and displays it under source.

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
        # Process the message
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # Parse and format the response
        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        match = re.search(source_pattern, answer_text, flags=re.DOTALL)

        # Try to parse the response as JSON first
        try:
            response_json = json.loads(answer_text)
            
            # Check if this is our expected JSON format with content and source
            if isinstance(response_json, dict) and "content" in response_json and "source" in response_json:
                # We have a structured JSON response!
                content_items = response_json["content"]
                source = response_json["source"]
                
                # Build the adaptive card body
                body_blocks = []
                
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
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": item.get("text", ""),
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
                
                # Create the source section
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
                            "text": f"Source: {source}",
                            "wrap": True,
                            "weight": "Bolder",
                            "color": "Accent",
                            "spacing": "Medium",
                        }
                    ]
                }
                
                # Add source details if they exist
                if "source_details" in response_json:
                    source_details = response_json["source_details"]
                    
                    # Format the source attribution based on type and available file names
                    source_attribution = ""
                    
                    # Use file_names and table_names if they exist
                    file_names = source_details.get("file_names", [])
                    table_names = source_details.get("table_names", [])
                    
                    # For Index sources, show file names if available
                    if source == "Index" and file_names:
                        # Ensure no duplicates and limit to 3
                        unique_files = []
                        for fname in file_names:
                            if fname not in unique_files:
                                unique_files.append(fname)
                        
                        if len(unique_files) > 3:
                            unique_files = unique_files[:3]
                            
                        source_attribution = "Referenced " + " and ".join(unique_files)
                    
                    # For Python sources, show table names if available
                    elif source == "Python" and table_names:
                        # Keep file extensions in table names for clarity
                        unique_tables = []
                        for name in table_names:
                            if name not in unique_tables:
                                unique_tables.append(name)
                        
                        if len(unique_tables) > 3:
                            unique_tables = unique_tables[:3]
                            
                        source_attribution = "Calculated using " + " and ".join(unique_tables)
                    
                    # For combined sources, show both if available
                    elif source == "Index & Python":
                        file_parts = []
                        table_parts = []
                        
                        # Ensure no duplicates in files and limit to 3
                        if file_names:
                            for fname in file_names:
                                if fname not in file_parts:
                                    file_parts.append(fname)
                            
                            if len(file_parts) > 3:
                                file_parts = file_parts[:3]
                            
                        # Ensure no duplicates in tables and limit to 3
                        if table_names:
                            for name in table_names:
                                if name not in table_parts:
                                    table_parts.append(name)
                            
                            if len(table_parts) > 3:
                                table_parts = table_parts[:3]
                        
                        if file_parts and table_parts:
                            source_attribution = f"Retrieved Using {' and '.join(table_parts)} and {' and '.join(file_parts)}"
                        elif file_parts:
                            source_attribution = "Referenced " + " and ".join(file_parts)
                        elif table_parts:
                            source_attribution = "Calculated using " + " and ".join(table_parts)
                    
                    # If source_attribution is still empty, fall back to the existing logic
                    if not source_attribution:
                        # We'll keep the default behavior by not adding a source attribution
                        pass
                        
                    # Add the attribution as the first item after the source
                    if source_attribution:
                        source_container["items"].insert(1, {
                            "type": "TextBlock",
                            "text": source_attribution,
                            "wrap": True,
                            "weight": "Bolder",
                            "spacing": "Small",
                            "color": "Good"
                        })
                        
                    # Add files information if available (original code)
                    if "files" in source_details and source_details["files"]:
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": "**Content Details:**",
                            "wrap": True,
                            "weight": "Bolder",
                            "spacing": "Medium"
                        })
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": source_details["files"],
                            "wrap": True,
                            "spacing": "Small",
                            "fontType": "Monospace",
                            "size": "Small"
                        })
                    
                    # Add code information if available (original code)
                    if "code" in source_details and source_details["code"]:
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": "**Code:**",
                            "wrap": True,
                            "weight": "Bolder",
                            "spacing": "Medium"
                        })
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": f"```\n{source_details['code']}\n```",
                            "wrap": True,
                            "spacing": "Small",
                            "fontType": "Monospace",
                            "size": "Small"
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
                await turn_context.send_activity(message)
                
                # Successfully processed JSON, so return early
                return
                
        except (json.JSONDecodeError, KeyError, TypeError):
            # Not JSON or not in our expected format, fall back to the regular processing
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
