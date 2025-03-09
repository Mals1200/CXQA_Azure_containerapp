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

# Conversation-specific chat histories
conversation_histories = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """
    This endpoint is for testing outside of Teams/Bot Framework.
    Important: We must consume the generator returned by Ask_Question().
    """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    question = data['question']

    # ask_func.Ask_Question(...) returns a generator, so let's consume it:
    answer_generator = Ask_Question(question)
    answer_text = ''.join(answer_generator)  # Turn all yielded chunks into one string

    return jsonify({'answer': answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    This endpoint is for handling messages from Microsoft Teams (Bot Framework).
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
    The bot logic callback. We must also consume the generator here.
    """
    # Identify conversation ID
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation history if it doesn't exist
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Overwrite the global chat_history in ask_func.py to isolate each conversation
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # ✅ Step 1: Send "Thinking..." message BEFORE processing
    thinking_activity = Activity(
        type="message",
        text="Thinking... ⏳"
    )
    await turn_context.send_activity(thinking_activity)


    answer_generator = Ask_Question(user_message)
    # Collect the chunks into a single string
    answer_text = ''.join(answer_generator)

    # Update conversation-specific history
    conversation_histories[conversation_id] = ask_func.chat_history

    # Check for a source section to decide how to render the response:
    if "Source:" in answer_text or "Source\n" in answer_text:
        # Split into main answer and source details
        import re
        source_match = re.split(r"\n*Source:\s*", answer_text, maxsplit=1)
        
        if len(source_match) > 1:
            main_answer = source_match[0].strip()
            source_details = "Source: " + source_match[1].strip()
        else:
            main_answer = answer_text.strip()
            source_details = None  # No valid source found

        # Build an Adaptive Card with a toggle to show/hide sources
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
            attachments=[{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": adaptive_card
            }]
        )
        await turn_context.send_activity(message)
    else:
        # No explicit source info, just send plain text
        await turn_context.send_activity(Activity(type="message", text=answer_text))


if __name__ == '__main__':
    # Important: host='0.0.0.0' + port=80 for Azure Container Instances
    app.run(host='0.0.0.0', port=80)
