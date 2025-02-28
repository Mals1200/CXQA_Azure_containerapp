# app.py

import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

from ask_func import Ask_Question, chat_history

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# PPT Export blueprint import
from ppt_export_agent import ppt_export_bp

app = Flask(__name__)

# Register the blueprint for PPT generation
app.register_blueprint(ppt_export_bp)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# This dict will store a small "state machine" per conversation: 
# e.g. { "conversation_id_abc123": {"waiting_for_ppt_instructions": True}}
conversation_states = {}

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
    # This code does not handle multiple conversation IDs; if you want that, 
    # you could pass a "conversation_id" or "user_id" in data, etc.
    # For simplicity, we do one global state.

    # 1) Check if we are currently waiting for instructions
    #    or if the user typed "export ppt"
    state_key = "global"  # If you want per-user, use user_id or conversation_id
    if state_key not in conversation_states:
        conversation_states[state_key] = {"waiting_for_ppt_instructions": False}

    # If we are waiting for instructions, assume the question is the instructions
    if conversation_states[state_key]["waiting_for_ppt_instructions"]:
        instructions = question.strip()
        conversation_states[state_key]["waiting_for_ppt_instructions"] = False

        # Now we have instructions, let's call the PPT creation route 
        # We'll do an internal request in the same Flask app:
        ppt_data = {"instructions": instructions}
        create_ppt_url = request.host_url.rstrip('/') + "/create_ppt"  # from ppt_export_agent.py
        try:
            ppt_response = requests.post(create_ppt_url, json=ppt_data, timeout=30)
            if ppt_response.status_code == 200:
                # The response is a file. We'll just return the direct content 
                # or a link to user. For demonstration, let's return a simple JSON 
                # that says "File created" and base64 or link. 
                # (You might do something else in production.)
                # For simplicity, let's just say "Your PPT has been generated. 
                # Please see the download link."

                # If you want to store the file or provide a direct link, you'd do it differently.
                return jsonify({
                    "answer": "Your PPT has been generated. (In a real UI, you'd present a download link or file.)"
                })
            else:
                return jsonify({"error": f"Failed to create PPT. Status {ppt_response.status_code}"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # 2) If user typed "export ppt", ask for instructions
    if question.strip().lower() == "export ppt":
        conversation_states[state_key]["waiting_for_ppt_instructions"] = True
        return jsonify({
            "answer": "Sure! Please provide detailed instructions for how you'd like your PPT to be structured."
        })

    # 3) Otherwise, handle the normal question/answer flow with ask_func
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

############################################
# Bot Framework code remains the same
############################################
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

    # *Optionally* do the same state-check logic as above for the bot 
    # if you want the same feature in Teams or Slack. 
    # For brevity, let's not replicate it here.

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)

    conversation_histories[conversation_id] = ask_func.chat_history
    await turn_context.send_activity(Activity(type="message", text=answer))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
