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

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

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
    answer_text = "".join(Ask_Question(question))  # Non-streaming endpoint.
    return jsonify({'answer': answer_text})

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
    """
    This version sends one message first, then updates it incrementally as tokens arrive.
    """
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Set the conversation-specific history in ask_func.py.
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Send an initial empty message.
    initial_activity = Activity(type="message", text="")
    sent_activity = await turn_context.send_activity(initial_activity)

    accumulated_text = ""
    # Process the answer token-by-token.
    async for token in _async_ask(user_message):
        accumulated_text += token
        updated_activity = Activity(
            id=sent_activity.id,
            type="message",
            text=accumulated_text
        )
        await turn_context.update_activity(updated_activity)
        # Small delay to avoid flooding updates.
        await asyncio.sleep(0.5)

    # Update conversation history after processing.
    conversation_histories[conversation_id] = ask_func.chat_history

# Helper: Wrap the synchronous generator as an asynchronous generator.
async def _async_ask(question: str):
    loop = asyncio.get_event_loop()
    for token in Ask_Question(question):
        # Yield each token; you could also add an await asyncio.sleep(0) here if needed.
        yield token
        await asyncio.sleep(0)  # allow the event loop to run

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
