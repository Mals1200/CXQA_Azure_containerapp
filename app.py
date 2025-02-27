import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Q&A logic (unchanged)
from ask_func import Ask_Question

# GPT-based PPT export logic
import ppt_export_agent

import uuid

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

########################################################################
# conversation_histories:
#   conversation_histories[conversation_id] -> list of chat messages
#   conversation_histories[conversation_id + "_answers"] -> dict{ answer_id: answer_text }
#   conversation_histories[conversation_id + "_questions"] -> dict{ answer_id: question_text }
########################################################################
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
    1. Check if user clicked an Action.Submit (like Export PPT).
    2. Otherwise, handle normal text messages -> ask_func.
    3. If greeting, no card is shown. If final answer has "Source:", we show "Show Source" + "Export PPT".
    """

    conversation_id = turn_context.activity.conversation.id

    # Ensure chat logs
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    # Answers
    answers_key = conversation_id + "_answers"
    if answers_key not in conversation_histories:
        conversation_histories[answers_key] = {}
    # Questions
    questions_key = conversation_id + "_questions"
    if questions_key not in conversation_histories:
        conversation_histories[questions_key] = {}

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # Check if user clicked an Adaptive Card button
    if turn_context.activity.value and "action" in turn_context.activity.value:
        action = turn_context.activity.value["action"]
        if action == "export":
            answer_id = turn_context.activity.value.get("answer_id", "")
            user_instructions = turn_context.activity.value.get("user_instructions", "")

            # Retrieve the stored answer
            stored_answers = conversation_histories[answers_key]
            answer_text = stored_answers.get(answer_id, "")
            if not answer_text:
                await turn_context.send_activity("No previous answer found to export.")
                return

            # Retrieve the stored question (optional)
            stored_questions = conversation_histories[questions_key]
            question_text = stored_questions.get(answer_id, "No question stored.")

            # Build chat history as string
            chat_history_str = "\n".join(ask_func.chat_history)

            # Now call the GPT-based PPT
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

    # Normal user text
    user_message = turn_context.activity.text or ""
    answer_text = Ask_Question(user_message)
    conversation_histories[conversation_id] = ask_func.chat_history

    # If this is a greeting, just output text
    if answer_text.startswith("Hello! I'm The CXQA AI Assistant") or \
       answer_text.startswith("Hello! How may I assist you"):
        await turn_context.send_activity(Activity(type="message", text=answer_text))
        return

    # Store the answer
    new_answer_id = str(uuid.uuid4())
    conversation_histories[answers_key][new_answer_id] = answer_text
    # Store the user question
    conversation_histories[questions_key][new_answer_id] = user_message

    # Now build an Adaptive Card
    if "\n\nSource:" in answer_text:
        # We split out the main part vs. source block
        parts = answer_text.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = "Source:" + parts[1].strip()

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
                },
                {
                    "type": "Action.Submit",
                    "title": "Export PPT with GPT",
                    "data": {
                        "action": "export",
                        "answer_id": new_answer_id,
                        # If you want instructions, you can prompt user for them:
                        "user_instructions": ""
                    }
                }
            ],
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }
    else:
        # No explicit "Source:" -> simpler card
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
