import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

conversation_histories = {}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request, "question" is required.'}), 400
    question = data["question"]

    ans_gen = Ask_Question(question)
    answer_text = "".join(ans_gen)
    return jsonify({"answer": answer_text})

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
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # 1) typing indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # 2) thinking symbol
    thinking_message = Activity(type="message", text="🤔 (thinking...)")
    await turn_context.send_activity(thinking_message)

    # get answer
    ans_gen = Ask_Question(user_message)
    answer_text = "".join(ans_gen)

    # update conversation history
    conversation_histories[conversation_id] = ask_func.chat_history

    # If there's "Source:" in answer_text, we might also have appended details.
    # We'll parse out the main answer, the "Source: ..." line, and appended details if present.
    import re
    source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)
    if match:
        main_answer = match.group(1).strip()
        source_line = match.group(2).strip()
        appended_details = match.group(3) if match.group(3) else ""
    else:
        main_answer = answer_text
        source_line = ""
        appended_details = ""

    if source_line:
        # Hide both the source line and appended details behind the same toggle
        body_blocks = [
            {
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            },
            {
                "type": "TextBlock",
                "text": source_line,
                "wrap": True,
                "id": "sourceLineBlock",
                "isVisible": False
            }
        ]

        if appended_details:
            body_blocks.append({
                "type": "TextBlock",
                "text": appended_details.strip(),
                "wrap": True,
                "id": "sourceBlock",
                "isVisible": False
            })

        actions = []
        if appended_details or source_line:
            actions = [
                {
                    "type": "Action.ToggleVisibility",
                    "title": "Show Source",
                    "targetElements": ["sourceLineBlock", "sourceBlock"]
                }
            ]

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": body_blocks,
            "actions": actions,
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
        # No "Source:" line, just return the plain text
        await turn_context.send_activity(Activity(type="message", text=main_answer))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
