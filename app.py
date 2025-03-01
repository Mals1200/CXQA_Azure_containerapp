import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Keep your existing Ask_Question function (from ask_func.py)
from ask_func import Ask_Question

# BotBuilder imports
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity

################################################################################
# NEW GLOBAL VARIABLES FOR "EXPORT PPT" FLOW
################################################################################
waiting_for_ppt_instructions = False  # Flag: Are we waiting for the user to give PPT instructions?

app = Flask(__name__)

# 1) Read Bot credentials from ENV
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# 2) Create settings & adapter for Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)


@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200


@app.route('/ask', methods=['POST'])
def ask():
    """
    Endpoint to handle user questions. If user says "Export PPT", we prompt for instructions
    and store that state in a global variable. On the next user message, we create the PPT.
    """
    global waiting_for_ppt_instructions

    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    user_message = data['question'].strip()

    # 1) If we are waiting for instructions, this user_message is the instructions
    if waiting_for_ppt_instructions:
        waiting_for_ppt_instructions = False

        instructions = user_message  # store the instructions
        # Now call PPT_Agent
        from PPT_Agent import Call_PPT
        link = Call_PPT(instructions=instructions)

        return jsonify({'answer': link})

    # 2) If user typed "Export PPT", we prompt them for instructions
    if user_message.lower() == "export ppt":
        waiting_for_ppt_instructions = True
        return jsonify({'answer': "Please add your instructions for the PPT now."})

    # 3) Otherwise do normal Q&A
    answer = Ask_Question(user_message)
    return jsonify({'answer': answer})


# =========================
# Bot Framework endpoint
# =========================
@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Bot Framework endpoint that the Bot Service calls (e.g., from Web Chat).
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
    """
    Bot logic for messages from Bot Framework. 
    If user typed "Export PPT", we rely on the same logic 
    in our /ask route above for the actual PPT handling.
    """
    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)
    await turn_context.send_activity(Activity(type="message", text=answer))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
