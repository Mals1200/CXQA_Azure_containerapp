# app.py
import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Import Ask_Question and chat_history from your unchanged ask_func.py
from ask_func import Ask_Question, chat_history

# Import the new ppt_export_agent
import ppt_export_agent

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

app = Flask(__name__)

# Read Bot credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create settings & adapter for the Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Global dictionary to maintain conversation-specific chat histories.
# Keys will be conversation IDs and values will be lists of chat history messages.
conversation_histories = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    question = data['question']
    # For non-bot messages, we simply use the global chat_history.
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)
    # Deserialize incoming Activity
    body = request.json
    activity = Activity().deserialize(body)
    # Get the Authorization header (for Bot Framework auth)
    auth_header = request.headers.get("Authorization", "")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()
    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id

    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Sync ask_func's global chat_history with this conversation's history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = (turn_context.activity.text or "").strip().lower()

    # ----------------------------------------------------------------
    # EXAMPLE: If user says "export ppt", we ask for instructions next.
    # ----------------------------------------------------------------
    if user_message == "export ppt":
        await turn_context.send_activity(
            Activity(type="message", text="Please provide instructions for the PPT slides.")
        )
        # We'll store a flag in your conversation state. This example is naive.
        # For demonstration, let's store a marker in the last message of chat history:
        ask_func.chat_history.append("Assistant: [PPT_EXPORT_INSTRUCTIONS_AWAITED]")
        conversation_histories[conversation_id] = ask_func.chat_history
        return

    # ---------------------------------------------------------------------
    # If we've asked the user for instructions (the marker is in chat_history),
    # let's interpret the next user message as PPT instructions.
    # ---------------------------------------------------------------------
    if "Assistant: [PPT_EXPORT_INSTRUCTIONS_AWAITED]" in ask_func.chat_history:
        # Extract the instructions from user_message
        ppt_instructions = user_message
        # Remove the marker from history
        ask_func.chat_history = [
            msg for msg in ask_func.chat_history 
            if "[PPT_EXPORT_INSTRUCTIONS_AWAITED]" not in msg
        ]

        # Call the export function from ppt_export_agent
        link = ppt_export_agent.export_ppt(ask_func.chat_history, ppt_instructions)

        # Present the link as a button in an Adaptive Card
        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {"type": "TextBlock", "text": "Your PowerPoint is ready for download!", "wrap": True}
            ],
            "actions": [
                {
                    "type": "Action.OpenUrl",
                    "title": "Export",
                    "url": link  # the direct PPT download link
                }
            ],
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }
        message = Activity(
            type="message",
            attachments=[{"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card}]
        )
        await turn_context.send_activity(message)

        # Save updated chat history
        conversation_histories[conversation_id] = ask_func.chat_history
        return

    # -----------------------------------------------------
    # Otherwise, handle normal Q&A with Ask_Question
    # -----------------------------------------------------
    answer = Ask_Question(user_message)
    conversation_histories[conversation_id] = ask_func.chat_history

    # Source handling
    if "\n\nSource:" in answer:
        parts = answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = "Source:" + parts[1].strip()

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {"type": "TextBlock", "text": main_answer, "wrap": True},
                {"type": "TextBlock", "text": source_details, "wrap": True, "id": "sourceBlock", "isVisible": False}
            ],
            "actions": [
                {"type": "Action.ToggleVisibility", "title": "Show Source", "targetElements": ["sourceBlock"]}
            ],
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }
        message = Activity(
            type="message",
            attachments=[{"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card}]
        )
        await turn_context.send_activity(message)
    else:
        await turn_context.send_activity(Activity(type="message", text=answer))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
