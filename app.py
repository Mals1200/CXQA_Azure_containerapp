import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Import the Ask_Question function from ask_func.py
from ask_func import Ask_Question

# BotBuilder imports
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity

app = Flask(__name__)

# Read Bot credentials from ENV
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create settings & adapter for Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# =========================
# Endpoints
# =========================
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)
    # Deserialize incoming Activity
    body = request.json
    activity = Activity().deserialize(body)
    # Grab the Authorization header (for Bot Framework auth)
    auth_header = request.headers.get("Authorization", "")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            adapter.process_activity(activity, auth_header, _bot_logic)
        )
    finally:
        loop.close()
    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    """
    This async function handles the incoming user message and sends back a reply.
    If the answer contains a source section (delimited by "\n\nSource:"), it sends an Adaptive Card
    with a "Show Source" button to toggle the display of the extra details.
    """
    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)
    
    if "\n\nSource:" in answer:
        # Split the answer into the main text and the source details.
        parts = answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = parts[1].strip()
        
        # Build an Adaptive Card with a hidden TextBlock for the source details.
        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True
                },
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
            attachments=[
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card
                }
            ]
        )
        await turn_context.send_activity(message)
    else:
        # If there is no source section, send a plain text message.
        await turn_context.send_activity(Activity(type="message", text=answer))

# =========================
# Gunicorn entry point
# =========================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
