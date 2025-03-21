import os
import asyncio
import logging

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity

# TeamsInfo is needed if you want to resolve user info from Teams
from botbuilder.core.teams import TeamsInfo

# Import your custom logic
from ask_func import Ask_Question, chat_history

# Set up basic logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Read in your Microsoft App credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Set up the Bot Framework Adapter
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# In-memory dict to store conversation histories by conversation ID
conversation_histories = {}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/api/messages", methods=["POST"])
def messages():
    """Entry point for all Teams (Bot Framework) messages."""
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
    """Main bot logic for handling Teams messages."""
    conversation_id = turn_context.activity.conversation.id

    # If we don't have a history for this conversation yet, create one
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Use the local reference so ask_func can see the right conversation history
    ask_func.chat_history = conversation_histories[conversation_id]
    user_message = turn_context.activity.text or ""
    logging.info(f"[TeamsBot] Received user message: {user_message}")

    # --------------------------------------------------
    # Attempt to resolve user identity from Teams
    # --------------------------------------------------
    user_id = "anonymous"
    try:
        teams_user_id = turn_context.activity.from_property.id
        teams_member = await TeamsInfo.get_member(turn_context, teams_user_id)
        if teams_member and teams_member.user_principal_name:
            user_id = teams_member.user_principal_name
        elif teams_member and teams_member.email:
            user_id = teams_member.email
        else:
            user_id = teams_user_id  # fallback
        logging.info(f"[TeamsBot] Resolved user ID: {user_id}")
    except Exception as e:
        logging.warning(f"[TeamsBot] Failed to get Teams member info: {e}")
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Show "typing" indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # --------------------------------------------------
    # Call your Q&A logic from ask_func
    # --------------------------------------------------
    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)  # accumulate generator into a single string
        logging.info(f"[TeamsBot] Answer text length: {len(answer_text)}")
    except Exception as ex:
        logging.error(f"[TeamsBot] ask_func error: {ex}")
        # Send a fallback message if an exception occurs
        await turn_context.send_activity(Activity(type="message", text="Sorry, an error occurred."))
        return

    # Update conversation history after generating answer
    conversation_histories[conversation_id] = ask_func.chat_history

    # If we have no final text, send a fallback
    if not answer_text.strip():
        logging.info("[TeamsBot] No answer text returned, sending fallback.")
        await turn_context.send_activity(Activity(type="message", text="I'm sorry, but I couldn't find an answer."))
        return

    # --------------------------------------------------
    # (Optional) Regex parse your 'Source:' line to show/hide in Adaptive Card
    # --------------------------------------------------
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

    # --------------------------------------------------
    # Send as an Adaptive Card if we have a "Source" line
    # --------------------------------------------------
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
        if appended_details.strip():
            body_blocks.append({
                "type": "TextBlock",
                "text": appended_details.strip(),
                "wrap": True,
                "id": "sourceBlock",
                "isVisible": False
            })

        actions = []
        if source_line or appended_details:
            actions.append({
                "type": "Action.ToggleVisibility",
                "title": "Show Source",
                "targetElements": ["sourceLineBlock", "sourceBlock"]
            })

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
        logging.info("[TeamsBot] Sent adaptive card with togglable source.")
    else:
        # Otherwise, just send plain text
        await turn_context.send_activity(Activity(type="message", text=main_answer))
        logging.info("[TeamsBot] Sent plain text message to user.")

if __name__ == "__main__":
    # Start the Flask app
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)
