# Version 9
# Fixed Files display in src

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
                    
                    # Format the source attribution based on type
                    source_attribution = ""
                    
                    # For Index sources, extract file names from the content
                    if source == "Index" and "files" in source_details and source_details["files"]:
                        file_content = source_details["files"]
                        
                        # First, look for actual filenames (patterns ending with common extensions)
                        file_patterns = [
                            # Specific pattern for the example file: DC_AM_Retail_102_Tier 2_Fire Evacuation SOP_Rev.000.pdf
                            r'(DC_[A-Za-z0-9_]+_\d+_Tier\s*\d+_[A-Za-z0-9_\s]+_Rev\.\d+\.pdf)',
                            r'(DC_[A-Za-z0-9_]+_[A-Za-z0-9_\s]+_Rev\.\d+\.pdf)',
                            # Generic patterns for filename detection
                            r'([A-Za-z0-9_\-]+_Rev\.\d+\.pdf)',
                            r'(DC_[A-Za-z0-9_\-]+\.pdf)',
                            r'([A-Za-z0-9_\-]+_Tier\s*\d+_[A-Za-z0-9_\s\-]+\.pdf)',
                            # Standard file extensions
                            r'([A-Za-z0-9_\-]+\.pdf)',
                            r'([A-Za-z0-9_\-]+\.docx?)',
                            r'([A-Za-z0-9_\-]+\.xlsx?)',
                            r'([A-Za-z0-9_\-]+\.csv)',
                            r'([A-Za-z0-9_\-]+\.txt)'
                        ]
                        
                        file_names = []
                        # Try to find filenames using regex patterns first
                        for pattern in file_patterns:
                            matches = re.findall(pattern, file_content, re.IGNORECASE)
                            if matches:
                                file_names.extend(matches)
                        
                        # If we didn't find any filenames using patterns, fall back to a better line extraction
                        if not file_names:
                            # Avoid lines that are clearly metadata
                            skip_patterns = [
                                r'approved date', r'document owner', r'revision', r'document prepared by',
                                r'effective date', r'review date', r'policy number', r'sop number',
                                r'^section', r'^chapter', r'^appendix', r'^table of contents'
                            ]
                            
                            for line in file_content.split('\n'):
                                line = line.strip()
                                # Skip empty lines, long text, or lines matching skip patterns
                                if not line or len(line) > 100:
                                    continue
                                    
                                should_skip = False
                                for skip in skip_patterns:
                                    if re.search(skip, line, re.IGNORECASE):
                                        should_skip = True
                                        break
                                
                                if not should_skip and not line.startswith('-') and not line.startswith('*'):
                                    # Check if this looks like a title (capitalize most words)
                                    if sum(1 for word in line.split() if word[0].isupper()) >= len(line.split())/2:
                                        file_names.append(line)
                                        # Only take the first good title-like line
                                        break
                        
                        # Deduplicate file names
                        file_names = list(dict.fromkeys(file_names))
                        
                        if file_names:
                            source_attribution = "Referenced " + " and ".join(file_names)
                        else:
                            source_attribution = "Referenced document"
                    
                    # For Python sources, extract table names from the code
                    elif source == "Python" and "code" in source_details and source_details["code"]:
                        code = source_details["code"]
                        table_names = []
                        
                        # Look for dataframe references like 'dataframes.get("TableName.xlsx")'
                        pattern = re.compile(r'dataframes\.get\(\s*[\'"]([^\'"]+)[\'"]\s*\)')
                        matches = pattern.findall(code)
                        
                        if matches:
                            table_names = [name.replace('.xlsx', '').replace('.csv', '') for name in matches]
                            source_attribution = "Calculated using " + " and ".join(table_names)
                        else:
                            source_attribution = "Calculated using data tables"
                    
                    # For combined sources
                    elif source == "Index & Python":
                        file_names = []
                        table_names = []
                        
                        # Extract file names from index
                        if "files" in source_details and source_details["files"]:
                            file_content = source_details["files"]
                            for line in file_content.strip().split('\n')[:5]:
                                line = line.strip()
                                if line and not line.startswith("---") and len(line) < 100:
                                    file_names.append(line)
                        
                        # Extract table names from code
                        if "code" in source_details and source_details["code"]:
                            code = source_details["code"]
                            pattern = re.compile(r'dataframes\.get\(\s*[\'"]([^\'"]+)[\'"]\s*\)')
                            matches = pattern.findall(code)
                            if matches:
                                table_names = [name.replace('.xlsx', '').replace('.csv', '') for name in matches]
                        
                        # Combine into attribution
                        if file_names and table_names:
                            source_attribution = f"Retrieved Using {', '.join(table_names)} and {', '.join(file_names)}"
                        elif file_names:
                            source_attribution = "Referenced " + " and ".join(file_names)
                        elif table_names:
                            source_attribution = "Calculated using " + " and ".join(table_names)
                        else:
                            source_attribution = "Retrieved from combined sources"
                    
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
                    
                    # Add files information if available (keep original but with a better header)
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
                    
                    # Add code information if available
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
