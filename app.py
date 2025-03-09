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
    """Handles incoming messages from Teams and streams responses."""

    # Retrieve the conversation ID from the incoming activity.
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation history if it doesn't exist.
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Before processing, override the chat_history in ask_func.py with this conversation's history.
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # ✅ (1) Send "Thinking..." message first (Informative Update)
    thinking_activity = Activity(
        type="message",
        text="Thinking... ⏳",
        entities=[{"type": "streaminfo", "streamType": "informative", "streamSequence": 1}]
    )
    await turn_context.send_activity(thinking_activity)

    # ✅ (2) Stream partial responses
    partial_answer = ""
    stream_sequence = 2  # Streaming sequence starts at 2
    async for token in Ask_Question(user_message):
        partial_answer += token

        # Send incremental updates
        streaming_activity = Activity(
            type="message",
            text=partial_answer,
            entities=[{
                "type": "streaminfo",
                "streamId": conversation_id,
                "streamType": "streaming",
                "streamSequence": stream_sequence
            }]
        )
        await turn_context.send_activity(streaming_activity)
        stream_sequence += 1  # Increase sequence for next update

    # ✅ (3) Format and send the final message
    if "\n\nSource:" in partial_answer:
        # Split into main answer and source details
        parts = partial_answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = "Source: " + parts[1].strip()  # ✅ Space after "Source:"

        # Adaptive Card for source display
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

        final_activity = Activity(
            type="message",
            attachments=[{"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card}],
            entities=[{
                "type": "streaminfo",
                "streamId": conversation_id,
                "streamType": "final"
            }]
        )
        await turn_context.send_activity(final_activity)

    else:
        # Send final text message if there's no source.
        final_activity = Activity(
            type="message",
            text=partial_answer,
            entities=[{
                "type": "streaminfo",
                "streamId": conversation_id,
                "streamType": "final"
            }]
        )
        await turn_context.send_activity(final_activity)

    # ✅ (4) Update conversation history
    conversation_histories[conversation_id] = ask_func.chat_history


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
