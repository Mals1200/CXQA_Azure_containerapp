import os
import re
import asyncio

from flask import Flask, request, jsonify, Response

# Bot Builder imports
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity

# For retrieving user info from Teams
from botbuilder.core.teams import TeamsInfo

# Import your Q&A function and chat history from ask_func.py
# Make sure ask_func.py is in the same folder or installed as a module
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

# Your Azure Bot App registration credentials
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Keep track of conversation histories for multi-user support
conversation_histories = {}


@app.route("/", methods=["GET"])
def home():
    """Root endpoint just to confirm API is alive."""
    return jsonify({"message": "API is running!"}), 200


@app.route("/ask", methods=["POST"])
def ask():
    """
    This route is for external usage (non-Teams).
    Expects JSON with:
      {
        "question": "some question",
        "user_email": "optional@domain.com"
      }

    If 'user_email' is missing, we default to 'anonymous'.
    """
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request: "question" is required.'}), 400

    question = data["question"]
    user_email = data.get("user_email", "anonymous")

    # Pass user_email into Ask_Question so it gets logged
    ans_gen = Ask_Question(question, user_id=user_email)
    answer_text = "".join(ans_gen)

    return jsonify({"answer": answer_text})


@app.route("/api/messages", methods=["POST"])
def messages():
    """
    This route is specifically for Microsoft Teams messages
    (the Bot Framework endpoint).
    """
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
    """
    The main bot logic for handling incoming Teams messages.
    Here we try to retrieve the user's email from Teams, then
    send their question to ask_func.Ask_Question(...).
    """
    conversation_id = turn_context.activity.conversation.id

    # If we don't have a history for this conversation yet, create one
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Link the global chat_history in ask_func to this conversation's array
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Default user ID/email to "anonymous"
    user_email = "anonymous"

    # Only try to get user info if we're in Microsoft Teams
    if turn_context.activity.channel_id == "msteams":
        teams_user_id = turn_context.activity.from_property.id
        try:
            # Attempt to retrieve more user details (including email)
            member = await TeamsInfo.get_member(turn_context, teams_user_id)
            if member:
                # Try to get email; if missing, try user_principal_name
                if member.email:
                    user_email = member.email
                elif member.user_principal_name:
                    user_email = member.user_principal_name
        except Exception as e:
            # If we fail (permissions, etc.), remain "anonymous"
            print(f"Could not retrieve user email from TeamsInfo: {e}")

    # Send the user's question to your Q&A function
    ans_gen = Ask_Question(question=user_message, user_id=user_email)
    answer_text = "".join(ans_gen)

    # Update conversation history with new content
    conversation_histories[conversation_id] = ask_func.chat_history

    # Optionally parse out "Source:" lines to produce an Adaptive Card
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
        # Example: show/hide the Source behind a toggle
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
            actions = [{
                "type": "Action.ToggleVisibility",
                "title": "Show Source",
                "targetElements": ["sourceLineBlock", "sourceBlock"]
            }]
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
        # No explicit "Source:" found, so just reply with the main answer text
        await turn_context.send_activity(Activity(type="message", text=main_answer))


if __name__ == "__main__":
    # Start the Flask server on port 80
    app.run(host="0.0.0.0", port=80)
