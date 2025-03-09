import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Import Ask_Question and chat_history from ask_func
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Store each conversation's chat history so multiple users won't overlap
conversation_histories = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """
    For testing outside of Teams/Bot Framework:
    Consumes the generator returned by Ask_Question(...).
    """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    question = data['question']
    # ask_func.Ask_Question(...) returns a generator. Collect all.
    answer_generator = Ask_Question(question)
    answer_text = ''.join(answer_generator)
    return jsonify({'answer': answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Endpoint for Microsoft Bot Framework (e.g. Teams).
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
    Bot logic callback. 
    We'll show a typing indicator, then produce a single final message with possible source info.
    """
    conversation_id = turn_context.activity.conversation.id

    # Initialize per-conversation history if not exist
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Overwrite the global chat_history with this conversation's history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Show typing indicator instead of a permanent "Thinking..." message
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # Now get the answer
    answer_generator = Ask_Question(user_message)
    answer_text = ''.join(answer_generator)

    # Update the conversation-specific history
    conversation_histories[conversation_id] = ask_func.chat_history

    # Attempt to parse for "Source:"
    if "Source:" in answer_text:
        # We'll split off the final line that contains "Source:"
        import re
        pattern = r"(.*?)\s*(Source:.*)"
        match = re.search(pattern, answer_text, flags=re.DOTALL)
        if match:
            main_answer = match.group(1).strip()
            source_line = match.group(2).strip()
        else:
            main_answer = answer_text.strip()
            source_line = ""

        # Simplify the card layout: main text + source line
        if source_line:
            adaptive_card = {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "TextBlock", "text": main_answer, "wrap": True},
                    {"type": "TextBlock", "text": source_line, "wrap": True}
                ],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.2"
            }
            message = Activity(
                type="message",
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card
                }]
            )
            await turn_context.send_activity(message)
        else:
            await turn_context.send_activity(Activity(type="message", text=main_answer))
    else:
        # No explicit source found
        await turn_context.send_activity(Activity(type="message", text=answer_text))

if __name__ == '__main__':
    # host='0.0.0.0' + port=80 for Azure Container Instances
    app.run(host='0.0.0.0', port=80)
