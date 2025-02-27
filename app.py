import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Your existing Q&A logic
from ask_func import Ask_Question

# The GPT-based PPT export module
import ppt_export_agent

import uuid

app = Flask(__name__)

# These can be empty strings if you aren't actually using them
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Bot adapter
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

#######################################################################
# conversation_histories is a dict:
#   conversation_histories[conversation_id] -> the chat messages (list)
#   conversation_histories[conversation_id + "_answers"] -> a dict
#       mapping answer_id (uuid) -> the GPT answer text
#   conversation_histories[conversation_id + "_questions"] -> a dict
#       mapping answer_id (uuid) -> the question that led to that answer (optional)
#######################################################################
conversation_histories = {}

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    """
    Direct endpoint to ask a question without Bot Framework (e.g., from Postman).
    """
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Bot Framework endpoint
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
    Main logic for each message:
    1. If user clicked "Export PPT" -> build PPT from GPT and answer.
    2. Otherwise, ask question normally, store answer with unique ID, show a card with "Export PPT."
    """

    conversation_id = turn_context.activity.conversation.id

    # Ensure a place to store chat messages
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    # Ensure a place to store answers
    answers_key = conversation_id + "_answers"
    if answers_key not in conversation_histories:
        conversation_histories[answers_key] = {}
    # Optionally store the question that led to each answer
    questions_key = conversation_id + "_questions"
    if questions_key not in conversation_histories:
        conversation_histories[questions_key] = {}

    # Sync with ask_func's chat_history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # Check if the user clicked an Adaptive Card Action.Submit
    if turn_context.activity.value and "action" in turn_context.activity.value:
        action = turn_context.activity.value["action"]

        if action == "export":
            # Retrieve the answer_id from the card data
            answer_id = turn_context.activity.value.get("answer_id", "")
            # If you want user instructions from the card, you'd parse them here too:
            user_instructions = turn_context.activity.value.get("user_instructions", "")

            # Get the text for that answer
            stored_answers = conversation_histories[answers_key]
            answer_text = stored_answers.get(answer_id, "")
            if not answer_text:
                await turn_context.send_activity("No previous answer found to export.")
                return

            # Also retrieve the question that led to that answer (optional)
            stored_questions = conversation_histories[questions_key]
            question_text = stored_questions.get(answer_id, "No question stored.")

            # Convert entire conversation to a single string
            chat_history_str = "\n".join(ask_func.chat_history)

            # Call the GPT-based PPT generation
            ppt_url = ppt_export_agent.generate_ppt_from_llm(
                question=question_text,
                answer_text=answer_text,
                chat_history_str=chat_history_str,
                instructions=user_instructions
            )

            if ppt_url:
                await turn_context.send_activity(f"Here is your GPT-based PPT link:\n{ppt_url}")
            else:
                await turn_context.send_activity("Sorry, I couldn't create the PPT file.")
        else:
            await turn_context.send_activity("Unknown action received.")
        return

    # Otherwise, it's a normal user text
    user_message = turn_context.activity.text or ""
    answer_text = Ask_Question(user_message)

    # Update the conversation's chat
    conversation_histories[conversation_id] = ask_func.chat_history

    # Detect if this is a greeting from the answer
    if answer_text.startswith("Hello! I'm The CXQA AI Assistant") or \
       answer_text.startswith("Hello! How may I assist you"):
        # Just send text, no "Export PPT"
        await turn_context.send_activity(Activity(type="message", text=answer_text))
        return

    # Store the new answer with a unique ID
    new_answer_id = str(uuid.uuid4())
    conversation_histories[answers_key][new_answer_id] = answer_text
    # Also store the user question that led to this answer (optional)
    conversation_histories[questions_key][new_answer_id] = user_message

    # Build an Adaptive Card with "Export PPT"
    # If you'd like user instructions, add a text field in the card:
    # For example, an Adaptive Card input:
    # {
    #   "type": "Input.Text",
    #   "id": "user_instructions",
    #   "placeholder": "Any instructions for your PPT?"
    # }
    # Then the data: {"action":"export","answer_id":..., "user_instructions":"${user_instructions}"}
    # For simplicity, we'll skip that here.

    adaptive_card = {
        "type": "AdaptiveCard",
        "body": [
            {"type": "TextBlock", "text": answer_text, "wrap": True}
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Export PPT with GPT",
                "data": {
                    "action": "export",
                    "answer_id": new_answer_id,
                    "user_instructions": ""
                }
            }
        ],
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2"
    }

    # If "Source:" is in the answer, you might also add a ToggleVisibility for the source block,
    # but we'll keep it simple here.

    message = Activity(
        type="message",
        attachments=[{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": adaptive_card
        }]
    )
    await turn_context.send_activity(message)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
