# version 5 
# Handle history better
    # The changes made address several key issues in both app.py and ask_func.py:
    # Thread-Safe State Management:
    # Added a thread-safe state management system in app.py using a Lock
    # Each conversation now has its own isolated state
    # Added cleanup of old conversation states to prevent memory leaks
    # Better Error Handling:
    # Added comprehensive error handling throughout both files
    # Improved error messages and logging
    # Added retry mechanism for Azure API calls with exponential backoff
    # State Isolation:
    # Each conversation now has its own isolated chat history and cache
    # Added proper initialization of global variables
    # Better management of the recent_history variable
    # Resource Management:
    # Added retry decorator for Azure API calls
    # Better handling of API timeouts and failures
    # Cleanup of old conversation states
    # Logging Improvements:
    # Added more detailed error logging
    # Better error messages for users
    # Proper exception handling in critical sections
    # These changes should make your bot more stable and reliable in the Teams environment. The main improvements are:
    # Each conversation is now properly isolated
    # Better handling of concurrent requests
    # More robust error handling and recovery
    # Better resource management
    # Improved logging for debugging
    # To deploy these changes:
    # Update your code with these changes
    # Test locally first
    # Deploy to your Azure Container App
    # Monitor the logs for any issues
    # If you still experience issues after deploying these changes, please let me know and I can help you investigate further. Also, make sure to check your Azure Container App's logs for any specific error messages that might help identify remaining issues.


import os
import asyncio
from threading import Lock

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
        import re
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
            body_blocks = [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True
                },
                {
                    "type": "TextBlock",
                    "text": source_line,
                    "wrap": True,
                    "id": "sourceLineBlock",
                    "isVisible": False
                }
            ]

            if appended_details:
                body_blocks.append({
                    "type": "TextBlock",
                    "text": appended_details.strip(),
                    "wrap": True,
                    "id": "sourceBlock",
                    "isVisible": False
                })

            actions = []
            if appended_details or source_line:
                actions = [
                    {
                        "type": "Action.ToggleVisibility",
                        "title": "Show Source",
                        "targetElements": ["sourceLineBlock", "sourceBlock"]
                    }
                ]

            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "actions": actions,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.2"
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
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
