import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Keep your existing imports from ask_func.py
from ask_func import Ask_Question

# Import our new PPT_Agent code
from PPT_Agent import Call_PPT

# BotBuilder imports
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity

app = Flask(__name__)

# 1) Read Bot credentials from ENV
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# 2) Create settings & adapter for Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# =========================
# Existing endpoints
# =========================
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200


@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    question = data['question']

    # 1) Check if user typed "Export PPT"
    if question.strip().lower() == "export ppt":
        # 1a) Call the Call_PPT() function directly
        link = Call_PPT()
        return jsonify({'answer': link})

    # 2) Otherwise, do normal Q&A via ask_func
    answer = Ask_Question(question)
    return jsonify({'answer': answer})


# =========================
# Bot Framework endpoint
# =========================
@app.route("/api/messages", methods=["POST"])
def messages():
    """
    This is the endpoint the Bot Service calls (e.g. from Web Chat).
    We must handle it asynchronously with 'adapter.process_activity'.
    """
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body = request.json
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            adapter.process_activity(activity, auth_header, _bot_logic)
        )
    finally:
        loop.close()

    return Response(status=200)


async def _bot_logic(turn_context: TurnContext):
    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)  # your existing Q&A logic
    await turn_context.send_activity(Activity(type="message", text=answer))


# =========================
# Gunicorn entry point
# =========================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
