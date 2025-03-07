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

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Global dictionary for conversation-specific chat histories
conversation_histories = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """
    REST endpoint for non-Bot calls.
    (This endpoint collects the full answer before returning.)
    """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    question = data['question']
    answer_text = "".join(Ask_Question(question))
    return jsonify({'answer': answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Bot Framework endpoint (e.g., for Microsoft Teams).
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

async def _async_ask(question: str):
    """
    Wrap the synchronous Ask_Question generator as an async generator.
    """
    for token in Ask_Question(question):
        yield token
        await asyncio.sleep(0)  # yield control

async def _bot_logic(turn_context: TurnContext):
    """
    Bot logic that streams a response by:
      1. Sending an initial "informative" typing activity with streamSequence 1.
      2. Updating that same activity periodically with accumulated tokens (using streamSequence increments and streamType "streaming").
      3. Sending a final update with type "message" and streamType "final".
    """
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    
    # Set conversation-specific chat history in ask_func
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""
    
    # --- 1. Start Streaming: Send initial informative update ---
    initial_entity = {
        "type": "streaminfo",
        "streamType": "informative",  # informative update
        "streamSequence": 1
    }
    initial_activity = Activity(
        type="typing",
        text="Searching through documents...",  # Informative loading message
        entities=[initial_entity]
    )
    sent_activity = await turn_context.send_activity(initial_activity)
    stream_id = sent_activity.id  # Use this as the streamId for subsequent updates

    accumulated_text = ""
    sequence_number = 2  # Next sequence number for subsequent streaming updates
    update_interval = 1.0  # Seconds between updates
    loop = asyncio.get_event_loop()
    next_update_time = loop.time() + update_interval

    # --- 2. Continue Streaming: Update with new tokens ---
    async for token in _async_ask(user_message):
        accumulated_text += token
        current_time = loop.time()
        if current_time >= next_update_time:
            update_entity = {
                "type": "streaminfo",
                "streamId": stream_id,
                "streamType": "streaming",  # streaming update
                "streamSequence": sequence_number
            }
            update_activity = Activity(
                id=stream_id,  # update the same activity
                type="typing",
                text=accumulated_text,
                entities=[update_entity]
            )
            try:
                await turn_context.update_activity(update_activity)
            except Exception as e:
                print(f"Error updating activity: {e}")
            sequence_number += 1
            next_update_time = current_time + update_interval

    # --- 3. Finalize Streaming: Send final message ---
    final_entity = {
        "type": "streaminfo",
        "streamId": stream_id,
        "streamType": "final"  # Mark final streaming update (no streamSequence)
    }
    final_activity = Activity(
        id=stream_id,
        type="message",  # Final message must be type "message"
        text=accumulated_text,
        entities=[final_entity]
    )
    await turn_context.update_activity(final_activity)

    # Update conversation history for this conversation
    conversation_histories[conversation_id] = ask_func.chat_history

if __name__ == '__main__':
    # Runs on port 80 (adjust as needed)
    app.run(host='0.0.0.0', port=80)
