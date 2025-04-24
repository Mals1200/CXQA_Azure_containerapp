# Version 7
# made source content different color(Blue) and segmented
# Button from "Show Source" to "Source"
# Fixed text formatting in Teams adaptive cards (still not quite there)
# Scrolling (not working)

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
    if not text or not isinstance(text, str):
        # Return a safe default if text is invalid
        return [{
            "type": "TextBlock",
            "text": str(text) if text is not None else "",
            "wrap": True
        }]
        
    try:
        # Replace markdown bold with actual bold formatting
        text = text.replace("**", "").replace("__", "")
        
        # Split the text into paragraphs
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()]
        
        text_blocks = []
        for paragraph in paragraphs:
            try:
                # Check if this paragraph contains a list
                lines = paragraph.split("\n")
                
                # Pattern for introductory text followed by numbered/bullet item
                # Handles patterns like: "Text: 1. Item" or "Text - First bullet"
                first_line = lines[0]
                intro_patterns = [
                    # Numbered lists with intro (e.g., "Follow these steps: 1. First item")
                    r'(.*?)(\d+\.\s+.*?)$',
                    # Bulleted lists with intro (e.g., "Key points: • First point")
                    r'(.*?)([\•\-\*]\s+.*?)$',
                    # Lettered lists with intro (e.g., "Consider these: a. First item")
                    r'(.*?)([a-zA-Z]\.\s+.*?)$'
                ]
                
                # Check for intro text with first list item
                intro_match = None
                for pattern in intro_patterns:
                    match = re.search(pattern, first_line)
                    if match:
                        intro_match = match
                        break
                
                if intro_match:
                    # Split the introduction from the first list item
                    intro = intro_match.group(1).strip()
                    first_item = intro_match.group(2).strip()
                    
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
                        if line.strip():
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
                
                # Handle regular lists where most lines start with a number, bullet, or letter
                if len(lines) > 1:
                    # Check if it's a list by looking at the majority of lines
                    list_patterns = [
                        r'^\d+\.', # Numbered list: "1. Item"
                        r'^[\•\-\*]', # Bullet list: "• Item" or "- Item"
                        r'^[a-zA-Z]\.', # Letter list: "a. Item"
                        r'^\s*[\-\•\*]' # Indented bullets
                    ]
                    
                    # Count how many lines match list patterns
                    list_line_count = 0
                    for line in lines:
                        for pattern in list_patterns:
                            if re.match(pattern, line.strip()):
                                list_line_count += 1
                                break
                    
                    # If at least 30% of lines look like list items, format as list
                    if list_line_count > 0 and list_line_count / len(lines) >= 0.3:
                        list_items = []
                        current_item = ""
                        
                        for line in lines:
                            is_list_item = False
                            for pattern in list_patterns:
                                if re.match(pattern, line.strip()):
                                    is_list_item = True
                                    break
                            
                            if is_list_item and current_item:
                                # Add previous item before starting new one
                                list_items.append({
                                    "type": "TextBlock",
                                    "text": current_item,
                                    "wrap": True,
                                    "spacing": "small"
                                })
                                current_item = line
                            elif is_list_item:
                                current_item = line
                            else:
                                # Continue previous list item (for wrapped text)
                                if current_item:
                                    current_item += " " + line.strip()
                                else:
                                    current_item = line
                        
                        # Add the last item
                        if current_item:
                            list_items.append({
                                "type": "TextBlock",
                                "text": current_item,
                                "wrap": True,
                                "spacing": "small"
                            })
                        
                        text_blocks.append({
                            "type": "Container",
                            "items": list_items,
                            "spacing": "medium"
                        })
                        continue
                
                # Handle one-liners as simple paragraphs
                if len(lines) == 1:
                    text_blocks.append({
                        "type": "TextBlock",
                        "text": paragraph,
                        "wrap": True,
                        "spacing": "medium"
                    })
                    continue
                
                # Default case: Multi-line paragraph that's not a list
                # First, check if it contains important terms that should be highlighted
                if any(re.search(r'(important|note|warning|caution|attention|remember):', line.lower()) for line in lines):
                    # Use emphasis style for important information
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
                        "style": "emphasis",
                        "items": list_items,
                        "spacing": "medium"
                    })
                else:
                    # Regular multi-line content
                    list_items = []
                    for line in lines:
                        if line.strip():
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
            except Exception as e:
                # Fallback for any paragraph that causes errors
                print(f"Error formatting paragraph: {str(e)}")
                text_blocks.append({
                    "type": "TextBlock",
                    "text": paragraph,
                    "wrap": True,
                    "spacing": "medium"
                })
        
        return text_blocks
    except Exception as e:
        # Global error handler - if anything goes wrong, return the text as a single block
        print(f"Error in format_text_for_adaptive_card: {str(e)}")
        return [{
            "type": "TextBlock",
            "text": text,
            "wrap": True
        }]

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
