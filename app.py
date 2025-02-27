import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# The Q&A logic
from ask_func import Ask_Question

# The GPT-based PPT generation
import ppt_export_agent

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

########################################################################
# We store the "last question" and "last answer" for each conversation
########################################################################
conversation_data = {
    # conversation_id: {
    #   "last_question": "...",
    #   "last_answer": "...",
    #   "chat_history": [...]
    # }
}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """ Direct usage endpoint (non-BotFramework) """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

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

    # Ensure we have some dict for this conversation
    if conversation_id not in conversation_data:
        conversation_data[conversation_id] = {
            "last_question": "",
            "last_answer": "",
            "chat_history": []
        }

    # Link with ask_func's chat_history
    import ask_func
    ask_func.chat_history = conversation_data[conversation_id]["chat_history"]

    user_message = (turn_context.activity.text or "").strip().lower()

    # If user typed "export ppt", do the PPT generation
    if user_message == "export ppt":
        # Use the last Q&A
        last_q = conversation_data[conversation_id]["last_question"]
        last_a = conversation_data[conversation_id]["last_answer"]
        if not last_a:
            await turn_context.send_activity("No previous answer found to export.")
            return

        chat_history_str = "\n".join(ask_func.chat_history)

        # Call the GPT-based PPT
        ppt_url = ppt_export_agent.generate_ppt_from_llm(
            question=last_q,
            answer_text=last_a,
            chat_history_str=chat_history_str,
            instructions=""
        )
        if ppt_url:
            await turn_context.send_activity(f"Here is your GPT-based PPT link:\n{ppt_url}")
        else:
            await turn_context.send_activity("Sorry, I couldn't create the PPT file.")
        return

    # Otherwise, treat it as a normal question
    question_text = turn_context.activity.text or ""
    answer_text = Ask_Question(question_text)

    # Update chat_history
    conversation_data[conversation_id]["chat_history"] = ask_func.chat_history

    # If greeting, just respond with text
    if answer_text.startswith("Hello! I'm The CXQA AI Assistant") or \
       answer_text.startswith("Hello! How may I assist you"):
        # store it, though
        conversation_data[conversation_id]["last_question"] = question_text
        conversation_data[conversation_id]["last_answer"] = answer_text
        await turn_context.send_activity(Activity(type="message", text=answer_text))
        return

    # store the Q & A
    conversation_data[conversation_id]["last_question"] = question_text
    conversation_data[conversation_id]["last_answer"] = answer_text

    # Return the final answer as plain text
    # Also tell them how to export PPT:
    final_message = (
        f"{answer_text}\n\n"
        "If you'd like to export this answer to PPT, please type: 'export ppt'"
    )
    await turn_context.send_activity(Activity(type="message", text=final_message))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
