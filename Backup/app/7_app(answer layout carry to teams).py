# Version 7
# Fixed text formatting in Teams adaptive cards
# Scrolling. (Does not work)
import os
import asyncio
from threading import Lock
import re

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

def format_text_for_adaptive_card(text):
    """
    Format text to preserve line breaks and markdown-style formatting
    for display in Teams adaptive cards.
    """
    # Replace markdown bold with actual bold formatting
    text = text.replace("**", "")
    
    # Split the text into paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    
    text_blocks = []
    for paragraph in paragraphs:
        # Check if this paragraph contains a list
        lines = paragraph.split("\n")
        
        # Look for patterns like "Follow these steps: 1. First item"
        # This detects if we have a sentence followed by a number list in the same line
        first_line = lines[0]
        if re.search(r'(.*?)(\d+\.\s+.*?)$', first_line):
            # Split the introduction from the first list item
            match = re.match(r'(.*?)(\d+\.\s+.*?)$', first_line)
            if match:
                intro = match.group(1).strip()
                first_item = match.group(2).strip()
                
                # Add the introduction as a separate text block
                if intro:
                    text_blocks.append({
                        "type": "TextBlock",
                        "text": intro,
                        "wrap": True,
                        "spacing": "medium"
                    })
                
                # Start a new list with the first item
                list_items = [{
                    "type": "TextBlock",
                    "text": first_item,
                    "wrap": True,
                    "spacing": "small"
                }]
                
                # Add remaining items to the list
                for line in lines[1:]:
                    list_items.append({
                        "type": "TextBlock",
                        "text": line,
                        "wrap": True,
                        "spacing": "small"
                    })
                
                text_blocks.append({
                    "type": "Container",
                    "items": list_items,
                    "spacing": "medium"
                })
                continue
        
        # Handle regular numbered lists (where each line starts with a number)
        if len(lines) > 1 and any(re.match(r'^\d+\.', line.strip()) for line in lines):
            list_items = []
            for line in lines:
                list_items.append({
                    "type": "TextBlock",
                    "text": line,
                    "wrap": True,
                    "spacing": "small"
                })
            text_blocks.append({
                "type": "Container",
                "items": list_items,
                "spacing": "medium"
            })
        elif len(lines) == 1:
            # Simple paragraph
            text_blocks.append({
                "type": "TextBlock",
                "text": paragraph,
                "wrap": True,
                "spacing": "medium"
            })
        else:
            # This might be a list or bullets, create a container for it
            list_items = []
            for line in lines:
                list_items.append({
                    "type": "TextBlock",
                    "text": line,
                    "wrap": True,
                    "spacing": "small"
                })
            text_blocks.append({
                "type": "Container",
                "items": list_items,
                "spacing": "medium"
            })
    
    return text_blocks

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

        if match:
            main_answer = match.group(1).strip()
            source_line = match.group(2).strip()
            appended_details = match.group(3) if match.group(3) else ""
        else:
            main_answer = answer_text
            source_line = ""
            appended_details = ""

        if source_line:
            # Format the main answer as multiple text blocks to preserve formatting
            body_blocks = format_text_for_adaptive_card(main_answer)
            
            # Create the collapsible source container
            if source_line or appended_details:
                # Create a container that will be toggled
                source_container = {
                    "type": "Container",
                    "id": "sourceContainer",
                    "isVisible": False,
                    "items": [
                        {
                            "type": "Container",
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
                        }
                    ]
                }
                
                # Add source details in a properly scrollable container if it exists
                if appended_details:
                    source_details_text_blocks = format_text_for_adaptive_card(appended_details.strip())
                    source_details_container = {
                        "type": "Container",
                        "style": "default",
                        "items": source_details_text_blocks,
                        "bleed": True
                    }
                    
                    # Wrap in a scrollable container
                    scrollable_container = {
                        "type": "Container",
                        "isScrollable": True,
                        "height": "auto",
                        "maxHeight": "250px",
                        "items": [source_details_container]
                    }
                    
                    source_container["items"].append(scrollable_container)
                
                body_blocks.append(source_container)
                
                # Simple button with no extra styling
                body_blocks.append({
                    "type": "ActionSet",
                    "actions": [
                        {
                            "type": "Action.ToggleVisibility",
                            "title": "Source",
                            "targetElements": ["sourceContainer"]
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
