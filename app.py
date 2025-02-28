import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Import your Q&A logic from ask_func
from ask_func import Ask_Question, chat_history

# Bot Framework
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Import PPT blueprint
from ppt_export_agent import ppt_export_bp

app = Flask(__name__)
app.register_blueprint(ppt_export_bp)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# We'll track conversation data in memory
conversation_histories = {}
conversation_states = {}  # track "waiting_for_ppt_instructions" or not

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    # This is your simple endpoint for direct usage without BotFramework
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
    The main logic for receiving messages from the user (Teams/Slack).
    """
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation memory if needed
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    if conversation_id not in conversation_states:
        conversation_states[conversation_id] = {"waiting_for_ppt_instructions": False}

    # Link the ask_func chat_history to this conversation
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # Check if we are currently waiting for PPT instructions
    if conversation_states[conversation_id]["waiting_for_ppt_instructions"]:
        # The user's message is presumably the instructions
        instructions = turn_context.activity.text.strip()
        conversation_states[conversation_id]["waiting_for_ppt_instructions"] = False

        # Now call the PPT creation route
        create_ppt_url = request.host_url.rstrip('/') + "/create_ppt"
        try:
            resp = requests.post(create_ppt_url, json={"instructions": instructions}, timeout=30)
            if resp.status_code == 200:
                # In a real scenario, resp is a .pptx file. 
                # We can't directly attach that in BotFramework easily. 
                # You might store it in a file share and provide a link, or something similar.
                # For simplicity, let's confirm success:
                await turn_context.send_activity("Your PPT has been generated. Please check your download.")
            else:
                await turn_context.send_activity(f"Failed to create PPT (status {resp.status_code}).")
        except Exception as e:
            await turn_context.send_activity(f"Error generating PPT: {e}")

        return  # done handling this message

    # If the user did NOT just provide instructions, let's see if they clicked the "Export PPT" button
    # The "value" is in turn_context.activity.value for an Action.Submit
    if turn_context.activity.value and "command" in turn_context.activity.value:
        cmd = turn_context.activity.value["command"]
        if cmd == "export_ppt":
            # Start the flow to get instructions
            conversation_states[conversation_id]["waiting_for_ppt_instructions"] = True
            await turn_context.send_activity("Please provide the instructions for your PowerPoint.")
            return

    # Otherwise, it's a normal user question
    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)
    conversation_histories[conversation_id] = ask_func.chat_history

    # If the answer has a "Source: " line, we build an adaptive card with two buttons:
    if "Source:" in answer:
        # Let's split out main answer from the Source details
        parts = answer.split("Source:")
        main_answer = parts[0].strip()
        source_details = "Source: " + parts[1].strip()

        # Build an adaptive card with:
        # - main_answer
        # - hidden source block
        # - "Show Source" (toggle)
        # - "Export PPT" (submit)

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True
                },
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
                    "title": "Export PPT",
                    "data": {"command": "export_ppt"}
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
    else:
        # No source in the answer, just send it as text
        await turn_context.send_activity(answer)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
