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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    answer = loop.run_until_complete(collect_answer(question))
    return jsonify({'answer': answer})


async def collect_answer(question):
    full_answer = ""
    async for token in Ask_Question(question):
        full_answer += token
    return full_answer

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
    partial_answer = ""
    update_interval = 20  # Every 20 tokens, send an update

    await turn_context.send_activity(Activity(type="typing"))  # Start typing indicator

    token_counter = 0
    try:
        async for token in Ask_Question(user_message):
            partial_answer += token
            token_counter += 1

            if token_counter % update_interval == 0:
                await turn_context.send_activity(Activity(type="message", text=partial_answer))

        # Final complete message
        await turn_context.send_activity(Activity(type="message", text=partial_answer))

    except Exception as e:
        await turn_context.send_activity(Activity(type="message", text=f"Error: {str(e)}"))

    # After processing, update the conversation-specific history.
    #  Update the conversation-specific history.
    conversation_histories[conversation_id] = ask_func.chat_history

    #  Check for "Source:" in the full streamed answer.
    if "\n\nSource:" in partial_answer:
        #  Split the answer into main content and source details.
        parts = partial_answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = "Source: " + parts[1].strip()

        #  Define the Adaptive Card.
        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True,
                    "weight": "Bolder",
                    "size": "Medium"
                },
                {
                    "type": "TextBlock",
                    "text": source_details,
                    "wrap": True,
                    "id": "sourceBlock",
                    "isVisible": False,
                    "spacing": "Medium"
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
            "version": "1.4"
        }

        #  Send the Adaptive Card as a response.
        message = Activity(
            type="message",
            attachments=[{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": adaptive_card
            }]
        )
        await turn_context.send_activity(message)

    else:
        #  If no source exists, just send the full answer as plain text.
        await turn_context.send_activity(Activity(type="message", text=partial_answer))



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
