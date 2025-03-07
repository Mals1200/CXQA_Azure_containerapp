import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Import Ask_Question and its global chat_history from your ask_func.py file
from ask_func import Ask_Question, chat_history

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

app = Flask(__name__)

# Read Bot credentials from environment variables (if using Azure Bot Service)
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create settings & adapter for the Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Global dictionary to maintain conversation-specific chat histories.
# Keys will be conversation IDs and values will be lists of chat messages.
conversation_histories = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """
    Simple REST endpoint for non-Bot calls:
    Accepts JSON: {"question": "..."}
    Returns JSON: {"answer": "..."}
    """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    question = data['question']

    # Ask_Question(...) returns a generator, so consume it with "".join(...)
    answer_text = "".join(Ask_Question(question))

    return jsonify({'answer': answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Bot Framework endpoint (e.g., Azure Bot Service).
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
    Logic for handling incoming Bot messages.
    """
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation-specific history if needed
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Override global chat_history in ask_func.py with conversation-specific history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Again, Ask_Question(...) returns a generator—consume it
    answer_generator = Ask_Question(user_message)
    answer_text = "".join(answer_generator)

    # Update the conversation-specific history
    conversation_histories[conversation_id] = ask_func.chat_history

    # If the answer contains a "Source:" marker, show an Adaptive Card with toggle
    if "\n\nSource:" in answer_text:
        parts = answer_text.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = "Source:" + parts[1].strip()

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {"type": "TextBlock", "text": main_answer, "wrap": True},
                {
                    "type": "TextBlock",
                    "text": source_details,
                    "wrap": True,
                    "id": "sourceBlock",
                    "isVisible": False
                }
            ],
            "actions": [
                {
                    "type": "Action.ToggleVisibility",
                    "title": "Show Source",
                    "targetElements": ["sourceBlock"]
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
    else:
        # If no Source marker, send as plain text
        await turn_context.send_activity(Activity(type="message", text=answer_text))

if __name__ == '__main__':
    # Runs on port 80 by default. Change to another port if needed.
    app.run(host='0.0.0.0', port=80)
