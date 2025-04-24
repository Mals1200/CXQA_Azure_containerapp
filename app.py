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
        
        # First, try to identify multiple sections with their own lists
        # Common patterns like "If you discover a fire, follow these steps:" followed by list items
        section_patterns = [
            r'(.*?follow these steps:.*?)(\d+\.|\-|\*|\•)',  # "follow these steps:" followed by numbered/bullet
            r'(.*?steps to follow:.*?)(\d+\.|\-|\*|\•)',     # "steps to follow:" followed by numbered/bullet
            r'(.*?For .*?:.*?)(\d+\.|\-|\*|\•)',             # "For fire suppression:" followed by numbered/bullet
            r'(.*?:)(\d+\..*)',                              # Any colon followed by a numbered item
        ]
        
        # Try to split text into sections with their own lists
        sections = []
        remaining_text = text
        
        for pattern in section_patterns:
            # Keep finding and extracting sections until no more matches
            while True:
                match = re.search(pattern, remaining_text, re.DOTALL)
                if not match:
                    break
                    
                # Get the introduction including the first list marker
                intro = match.group(1).strip()
                # Find where the next section might start
                next_section_pos = -1
                for p in section_patterns:
                    next_match = re.search(p, remaining_text[match.end():], re.DOTALL)
                    if next_match:
                        pos = match.end() + next_match.start()
                        if next_section_pos == -1 or pos < next_section_pos:
                            next_section_pos = pos
                
                if next_section_pos > 0:
                    # Extract this complete section
                    section_text = remaining_text[:next_section_pos]
                    remaining_text = remaining_text[next_section_pos:]
                else:
                    # This is the last section
                    section_text = remaining_text
                    remaining_text = ""
                
                sections.append(section_text)
                
                if not remaining_text:
                    break
        
        # If no sections were found, treat the entire text as one section
        if not sections:
            sections = [text]
        elif remaining_text:
            # Add any remaining text as the final section
            sections.append(remaining_text)
        
        # Now process each section
        text_blocks = []
        
        for section in sections:
            # Split the section into intro and list items
            list_start = -1
            list_patterns = [
                r'\d+\.', # Numbered list: "1. Item"
                r'[\•\-\*]', # Bullet list: "• Item" or "- Item"
                r'[a-zA-Z]\.', # Letter list: "a. Item"
            ]
            
            for pattern in list_patterns:
                match = re.search(r'(' + pattern + r'\s+)', section)
                if match and (list_start == -1 or match.start() < list_start):
                    list_start = match.start()
            
            if list_start > 0:
                intro = section[:list_start].strip()
                list_text = section[list_start:].strip()
                
                # Add the introduction as a separate text block
                if intro:
                    # Check if this intro contains "follow these steps:" or similar
                    if re.search(r'(follow these steps:|steps to follow:|for.*?:|if you.*?:)', intro, re.IGNORECASE):
                        text_blocks.append({
                            "type": "TextBlock",
                            "text": intro,
                            "wrap": True,
                            "spacing": "medium",
                            "weight": "bolder"  # Make section headings bold
                        })
                    else:
                        text_blocks.append({
                            "type": "TextBlock",
                            "text": intro,
                            "wrap": True,
                            "spacing": "medium"
                        })
                
                # Process the list items, preserving original numbering/bullets
                list_items = []
                for line in list_text.split('\n'):
                    if line.strip():
                        list_items.append({
                            "type": "TextBlock",
                            "text": line.strip(),
                            "wrap": True,
                            "spacing": "small"
                        })
                
                if list_items:
                    text_blocks.append({
                        "type": "Container",
                        "items": list_items,
                        "spacing": "small"
                    })
            else:
                # This section doesn't seem to have list items
                # Just add as regular text block
                text_blocks.append({
                    "type": "TextBlock",
                    "text": section.strip(),
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
