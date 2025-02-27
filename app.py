import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Q&A logic (unchanged) 
from ask_func import Ask_Question

# GPT-based PPT generation
import ppt_export_agent

import uuid

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

##########################################################################################
# We store the last Q, A, Source, and entire chat history in a dict for each conversation:
# conversation_data[conversation_id] = {
#   "last_question": "...",
#   "last_answer": "...",   # answer without the "Source:"
#   "last_source": "...",   # just the "Source:" portion
#   "chat_history": [ ... ] # same as ask_func.chat_history
# }
##########################################################################################
conversation_data = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    # Non-BotFramework usage
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

@app.route("/api/messages", methods=["POST"])
def messages():
    # BotFramework endpoint
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
    - If user typed "show source", we display last_source in text.
    - If user typed "export ppt", we generate PPT from last Q&A + entire chat.
    - Otherwise, we treat as normal question -> ask_func -> store Q, A, Source.
    - We build an Adaptive Card with "Show Source" (ToggleVisibility) + "Export PPT" (Submit).
    - Channels that don't support it => fallback to text commands.
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

    # Link ask_func's chat_history to this conversation
    import ask_func
    ask_func.chat_history = conversation_data[conversation_id]["chat_history"]

    user_message = (turn_context.activity.text or "").strip().lower()

    # -----------------------------------------------------------------
    # 1) Fallback text commands
    # -----------------------------------------------------------------
    if user_message == "show source":
        # Return last_source if available
        source = conversation_data[conversation_id]["last_source"]
        if source:
            await turn_context.send_activity(source)
        else:
            await turn_context.send_activity("No source available for the last answer.")
        return

    if user_message == "export ppt":
        last_q = conversation_data[conversation_id]["last_question"]
        last_a = conversation_data[conversation_id]["last_answer"]
        if not last_a:
            await turn_context.send_activity("No previous answer found to export.")
            return
        # Entire chat
        chat_history_str = "\n".join(ask_func.chat_history)
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

    # -----------------------------------------------------------------
    # 2) Otherwise, treat as normal question
    # -----------------------------------------------------------------
    question_text = turn_context.activity.text or ""
    answer_text = Ask_Question(question_text)
    # Update chat_history
    conversation_data[conversation_id]["chat_history"] = ask_func.chat_history

    # If greeting, just store & respond
    if answer_text.startswith("Hello! I'm The CXQA AI Assistant") or \
       answer_text.startswith("Hello! How may I assist you"):
        conversation_data[conversation_id]["last_question"] = question_text
        conversation_data[conversation_id]["last_answer"] = answer_text
        conversation_data[conversation_id]["last_source"] = ""
        await turn_context.send_activity(Activity(type="message", text=answer_text))
        return

    # Check if there's "\n\nSource:" in the answer
    source_index = answer_text.find("\n\nSource:")
    if source_index >= 0:
        main_answer = answer_text[:source_index].strip()
        source_text = answer_text[source_index:].strip()
    else:
        main_answer = answer_text
        source_text = ""

    # Store them
    conversation_data[conversation_id]["last_question"] = question_text
    conversation_data[conversation_id]["last_answer"] = main_answer
    conversation_data[conversation_id]["last_source"] = source_text

    # Build the final displayed text
    displayed_text = main_answer
    if source_text:
        # We'll hide the source behind a button, fallback text for channels ignoring AC
        displayed_text += "\n\n(You can click 'Show Source' or type 'show source'.)"
    # Also mention 'Export PPT'
    displayed_text += "\n\n(You can click 'Export PPT' or type 'export ppt'.)"

    # Build Adaptive Card with 2 buttons: "Show Source" (ToggleVisibility) & "Export PPT" (Submit)
    actions = []
    if source_text:
        actions.append({
            "type": "Action.ToggleVisibility",
            "title": "Show Source",
            "targetElements": ["sourceBlock"]
        })
    actions.append({
        "type": "Action.Submit",
        "title": "Export PPT",
        "data": {
            "action": "export"
        }
    })

    card_body = [
        {"type": "TextBlock", "text": main_answer, "wrap": True}
    ]
    if source_text:
        card_body.append({
            "type": "TextBlock",
            "text": source_text,
            "wrap": True,
            "id": "sourceBlock",
            "isVisible": False
        })

    adaptive_card = {
        "type": "AdaptiveCard",
        "fallbackText": (
            "If you can't see buttons, type:\n"
            "  'show source' to see the source,\n"
            "  'export ppt' to export the PPT.\n"
        ),
        "body": card_body,
        "actions": actions,
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2"
    }

    message = Activity(
        type="message",
        text=displayed_text,  # fallback text
        attachments=[{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": adaptive_card
        }]
    )
    await turn_context.send_activity(message)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
