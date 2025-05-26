# Simple Teams Bot App - Clean JSON Display
# Imports from ask_func.py and displays responses in organized format

import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity
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

    # Set the conversation state for this request
    import ask_func
    ask_func.chat_history = state['history']
    ask_func.tool_cache = state['cache']

    user_message = turn_context.activity.text or ""

    # Get user ID
    user_id = "anonymous"
    try:
        teams_user_id = turn_context.activity.from_property.id
        teams_member = await TeamsInfo.get_member(turn_context, teams_user_id)
        if teams_member and teams_member.user_principal_name:
            user_id = teams_member.user_principal_name
        elif teams_member and teams_member.email:
            user_id = teams_member.email
        else:
            user_id = teams_user_id
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Show typing indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    try:
        # Get response from ask_func
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # Check if we have any response
        if not answer_text or not answer_text.strip():
            await turn_context.send_activity(Activity(type="message", text="I'm sorry, I couldn't generate a response. Please try again."))
            return

        # Try to parse as JSON first
        response_json = None
        try:
            # Clean up the response text
            cleaned_text = answer_text.strip()
            
            # Remove code block markers
            if cleaned_text.startswith('```json'):
                cleaned_text = cleaned_text[7:].strip()
            elif cleaned_text.startswith('```'):
                cleaned_text = cleaned_text[3:].strip()
            if cleaned_text.endswith('```'):
                cleaned_text = cleaned_text[:-3].strip()
            
            # Try to find and extract JSON
            if '{' in cleaned_text and '}' in cleaned_text:
                start_idx = cleaned_text.find('{')
                end_idx = cleaned_text.rfind('}') + 1
                json_part = cleaned_text[start_idx:end_idx]
                response_json = json.loads(json_part)
                
                # Verify it has the expected structure
                if not (isinstance(response_json, dict) and "content" in response_json and "source" in response_json):
                    response_json = None
                    
        except (json.JSONDecodeError, KeyError, TypeError):
            response_json = None

        if response_json:
            # Display JSON response in organized format
            await display_json_response(turn_context, response_json)
        else:
            # Display as plain text with basic formatting
            await display_text_response(turn_context, answer_text)

    except Exception as e:
        error_message = f"An error occurred: {str(e)}"
        print(f"Error: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

async def display_json_response(turn_context: TurnContext, response_json):
    """Display JSON response in organized adaptive card format"""
    content_items = response_json.get("content", [])
    source = response_json.get("source", "Unknown")
    
    body_blocks = []
    
    # Process each content item
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
            if text and not (text.startswith("Referenced:") or text.startswith("Calculated using:")):
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

    # Add source information
    source_container = {
        "type": "Container",
        "id": "sourceContainer",
        "isVisible": False,
        "style": "emphasis",
        "items": [
            {
                "type": "TextBlock",
                "text": f"Source: {source}",
                "wrap": True,
                "weight": "Bolder",
                "color": "Accent"
            }
        ]
    }
    
    # Add file references if present
    for item in content_items:
        if item.get("type") == "paragraph":
            text = item.get("text", "")
            if text.startswith("Referenced:") or text.startswith("Calculated using:"):
                lines = text.split("\n")
                for line in lines:
                    if line.strip():
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": line,
                            "wrap": True,
                            "spacing": "Small"
                        })
    
    body_blocks.append(source_container)
    
    # Add show/hide source button
    body_blocks.append({
        "type": "ActionSet",
        "actions": [
            {
                "type": "Action.ToggleVisibility",
                "title": "Show/Hide Source",
                "targetElements": ["sourceContainer"]
            }
        ]
    })

    # Create and send adaptive card
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

async def display_text_response(turn_context: TurnContext, answer_text):
    """Display plain text response with basic formatting"""
    
    # Try to extract source information
    source_pattern = r"(.*?)\s*(Source:.*?)$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)
    
    if match:
        main_answer = match.group(1).strip()
        source_line = match.group(2).strip()
    else:
        main_answer = answer_text
        source_line = "Source: AI Generated"

    # Create simple adaptive card
    body_blocks = [
        {
            "type": "TextBlock",
            "text": main_answer,
            "wrap": True
        },
        {
            "type": "Container",
            "id": "sourceContainer",
            "isVisible": False,
            "style": "emphasis",
            "items": [
                {
                    "type": "TextBlock",
                    "text": source_line,
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Accent"
                }
            ]
        },
        {
            "type": "ActionSet",
            "actions": [
                {
                    "type": "Action.ToggleVisibility",
                    "title": "Show/Hide Source",
                    "targetElements": ["sourceContainer"]
                }
            ]
        }
    ]

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80) 
