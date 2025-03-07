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
    Simple REST endpoint for non-Bot calls.
    Accepts JSON: {"question": "..."}
    Returns JSON: {"answer": "..."}
    (Note: This endpoint still collects the full answer before returning.)
    """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    question = data['question']
    # Here we still join the tokens for a single response:
    answer_text = "".join(Ask_Question(question))
    return jsonify({'answer': answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Bot Framework endpoint (for Microsoft Teams, etc.).
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
    Bot logic that streams the answer token-by-token by sending each token as its own message.
    This simulates streaming to the user (each token is sent as a separate Activity).
    """
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation-specific history if needed
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    
    # Override global chat_history in ask_func.py with conversation-specific history.
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Instead of collecting all tokens and sending a single message, we send each token separately.
    for token in Ask_Question(user_message):
        # Optional: Uncomment the following line to add a small delay between tokens.
        # await asyncio.sleep(0.1)
        await turn_context.send_activity(Activity(type="message", text=token))

    # Update conversation history after processing.
    conversation_histories[conversation_id] = ask_func.chat_history

if __name__ == '__main__':
    # Runs on port 80 by default. Change the port if needed.
    app.run(host='0.0.0.0', port=80)
