import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Import Ask_Question and its global chat_history from your unchanged ask_func.py
from ask_func import Ask_Question, chat_history

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
    # Retrieve the conversation ID from the incoming activity.
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation history for this conversation if it doesn't exist.
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Before processing, override the chat_history in ask_func.py with this conversation's history.
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)

    # After processing, update the conversation-specific history.
    conversation_histories[conversation_id] = ask_func.chat_history

    # Check if the answer contains a source section (using "\n\nSource:" as a marker)
    if "\n\nSource:" in answer:
        # Split into main answer and source details
        parts = answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        
        # Optionally, prepend "Source:" to the details
        source_details = "Source:" + parts[1].strip()

        # Build an Adaptive Card with the main answer and a hidden block for the source details.
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
        # Send plain text answer if no source section is detected.
        await turn_context.send_activity(Activity(type="message", text=answer))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
