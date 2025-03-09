import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
# Import from ask_func
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Per-conversation chat histories
conversation_histories = {}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/ask", methods=["POST"])
def ask():
    """
    For testing outside of Teams/Bot Framework:
    """
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({'error': 'Invalid request, "question" is required.'}), 400
    question = data["question"]

    ans_gen = Ask_Question(question)
    answer_text = ''.join(ans_gen)
    return jsonify({"answer": answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Teams / Bot Framework endpoint.
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
    # Identify conversation
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Bind ask_func's global chat_history to this conversation's history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Show a typing indicator briefly
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # Now get the final answer
    ans_gen = Ask_Question(user_message)
    answer_text = ''.join(ans_gen)

    # Save updated history
    conversation_histories[conversation_id] = ask_func.chat_history

    # If there's a "Source:" line, let's create an adaptive card with a toggle
    if "Source:" in answer_text:
        import re
        # E.g. split on "Source:"
        pattern = r"(.*?)\s*(Source:.*)"
        match = re.search(pattern, answer_text, flags=re.DOTALL)
        if match:
            main_answer = match.group(1).strip()
            source_line = match.group(2).strip()
        else:
            main_answer = answer_text.strip()
            source_line = ""

        # We'll show the "Show Source" toggle if we have something
        if source_line:
            adaptive_card = {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "TextBlock", "text": main_answer, "wrap": True},
                    {
                        "type": "TextBlock",
                        "text": source_line,
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
            msg = Activity(
                type="message",
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card
                }]
            )
            await turn_context.send_activity(msg)
        else:
            # If no source
            await turn_context.send_activity(Activity(type="message", text=main_answer))
    else:
        # No source line, just return normal text
        await turn_context.send_activity(Activity(type="message", text=answer_text))

if __name__ == '__main__':
    # For Azure Container Instances
    app.run(host='0.0.0.0', port=80)
