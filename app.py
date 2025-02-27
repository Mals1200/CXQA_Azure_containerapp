import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Q&A logic (unchanged from your ask_func.py)
from ask_func import Ask_Question

# GPT-based PPT generation with your fake keys
import ppt_export_agent

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

##################################################################################
# conversation_data storage (per conversation_id):
# {
#   "last_question": "...",
#   "last_answer": "...",
#   "last_source": "...",   # if any
#   "chat_history": [...]
# }
##################################################################################
conversation_data = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """
    Direct usage endpoint for non-BotFramework usage.
    """
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
    """
    We do everything in text. 
    Commands:
      - normal question => run ask_func
      - "show source" => show last_source (if any)
      - "export ppt" => generate PPT from entire chat, last question, last answer
    """
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation data if not present
    if conversation_id not in conversation_data:
        conversation_data[conversation_id] = {
            "last_question": "",
            "last_answer": "",
            "last_source": "",
            "chat_history": []
        }

    # Link with ask_func
    import ask_func
    ask_func.chat_history = conversation_data[conversation_id]["chat_history"]

    user_message = (turn_context.activity.text or "").strip()

    ############################################################################
    # 1. If user typed "show source"
    ############################################################################
    if user_message.lower() == "show source":
        # Return the last_source if we have any
        last_source = conversation_data[conversation_id]["last_source"]
        if last_source:
            await turn_context.send_activity(last_source)
        else:
            await turn_context.send_activity("No source available for the last answer.")
        return

    ############################################################################
    # 2. If user typed "export ppt"
    ############################################################################
    if user_message.lower() == "export ppt":
        last_q = conversation_data[conversation_id]["last_question"]
        last_a = conversation_data[conversation_id]["last_answer"]
        if not last_a:
            await turn_context.send_activity("No previous answer found to export.")
            return

        # Build the entire chat as a single string
        chat_history_str = "\n".join(ask_func.chat_history)

        # Generate PPT
        ppt_url = ppt_export_agent.generate_ppt_from_llm(
            question=last_q,
            answer_text=last_a,
            chat_history_str=chat_history_str,
            instructions=""  # or prompt user for instructions in another step
        )
        if ppt_url:
            await turn_context.send_activity(f"Here is your GPT-based PPT link:\n{ppt_url}")
        else:
            await turn_context.send_activity("Sorry, I couldn't create the PPT file.")
        return

    ############################################################################
    # 3. Otherwise, treat user_message as a normal question
    ############################################################################
    question_text = user_message
    answer_text = Ask_Question(question_text)

    # Update the chat_history with ask_func
    conversation_data[conversation_id]["chat_history"] = ask_func.chat_history

    # If greeting, just respond and store
    if answer_text.startswith("Hello! I'm The CXQA AI Assistant") or \
       answer_text.startswith("Hello! How may I assist you"):
        conversation_data[conversation_id]["last_question"] = question_text
        conversation_data[conversation_id]["last_answer"] = answer_text
        conversation_data[conversation_id]["last_source"] = ""
        await turn_context.send_activity(answer_text)
        return

    # We check if the answer has "\n\nSource:"
    # If so, we split it out, store the source, and remove it from the displayed portion
    source_index = answer_text.find("\n\nSource:")
    if source_index != -1:
        main_answer = answer_text[:source_index].strip()
        source_text = answer_text[source_index:].strip()  # includes "\n\nSource:..."
    else:
        main_answer = answer_text
        source_text = ""

    # Store Q, A, Source
    conversation_data[conversation_id]["last_question"] = question_text
    conversation_data[conversation_id]["last_answer"] = main_answer
    conversation_data[conversation_id]["last_source"] = source_text

    # Build final message to user
    final_msg = main_answer
    if source_text:
        final_msg += "\n\n(Type 'show source' to view the source.)"
    final_msg += "\n\n(Type 'export ppt' to create a PPT from this answer.)"

    await turn_context.send_activity(final_msg)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
