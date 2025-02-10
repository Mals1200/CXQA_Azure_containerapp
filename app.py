import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Import the Ask_Question function from your unchanged ask_func.py
from ask_func import Ask_Question

# BotBuilder imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

app = Flask(__name__)

# Read Bot credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create settings & adapter for the Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# =========================
# API Endpoints
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
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()
    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    """
    Processes the incoming user message and sends back an Adaptive Card if the answer contains a source section.
    If the answer from Ask_Question contains "\n\nSource:" (as produced by your ask_func.py),
    the Adaptive Card will show the main answer and include a "Show Source" button to reveal the details.
    """
    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)
    
    # Check if the answer contains a source section (assuming it is appended with "\n\nSource:" followed by extra info)
    if "\n\nSource:" in answer:
        # Split the answer into the main part and the source details.
        parts = answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        # You can re-add the marker if you wish or simply show the details.
        source_details = "Source:" + parts[1].strip()
        
        # Build an Adaptive Card with the main answer and a hidden TextBlock for the source details.
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
        # If no source details are found, just send the plain text answer.
        await turn_context.send_activity(Activity(type="message", text=answer))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
