import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Import your existing Q&A logic from ask_func.py
from ask_func import Ask_Question

# Additional libraries for PPT
from pptx import Presentation
import uuid
from azure.storage.blob import BlobServiceClient

app = Flask(__name__)

# Bot credentials from environment variables (placeholder if empty)
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create settings & adapter for the Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

######################################################################
# conversation_histories is a dict:
#   conversation_histories[conversation_id] -> the chat messages (list)
#   conversation_histories[conversation_id + "_answers"] -> a dict
#       mapping answer_id (uuid) -> the GPT answer text
######################################################################
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
    # Deserialize incoming Activity
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
    Main logic to handle user messages:
    1. For normal text: Ask_Question -> store answer -> show a card (unless greeting).
    2. If user clicks "Export PPT", we retrieve the correct answer from stored answers by its ID.
    """
    conversation_id = turn_context.activity.conversation.id

    # Ensure we have chat message list
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    # Ensure we have an answers dict
    answers_key = conversation_id + "_answers"
    if answers_key not in conversation_histories:
        conversation_histories[answers_key] = {}

    # Use the conversation-specific chat_history in ask_func
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # Check if user clicked an action button
    if turn_context.activity.value and "action" in turn_context.activity.value:
        # It's an Action.Submit
        action = turn_context.activity.value["action"]
        if action == "export":
            # They want to export a PPT
            answer_id = turn_context.activity.value.get("answer_id", "")
            stored_answers = conversation_histories[answers_key]
            text_for_ppt = stored_answers.get(answer_id, "")

            if not text_for_ppt:
                await turn_context.send_activity("No previous answer found to export.")
                return

            # Generate PPT
            ppt_url = generate_and_upload_ppt(text_for_ppt)
            if ppt_url:
                await turn_context.send_activity(
                    f"I've generated your PPT! You can download it here:\n{ppt_url}"
                )
            else:
                await turn_context.send_activity("Sorry, I couldn't create the PPT file.")
        else:
            await turn_context.send_activity("Unknown action received.")
        return

    # Otherwise, it's normal text
    user_message = turn_context.activity.text or ""
    answer_text = Ask_Question(user_message)
    # update ask_func's chat_history
    conversation_histories[conversation_id] = ask_func.chat_history

    # STEP 1: Check if the answer is a greeting. If so, just send plain text
    # The typical greeting answers from ask_func might be:
    #  "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"
    #  "Hello! How may I assist you?"
    # We'll look for a simpler approach, just check if the answer_text starts with "Hello!"
    if answer_text.startswith("Hello! I'm The CXQA AI Assistant") or answer_text.startswith("Hello! How may I assist you"):
        await turn_context.send_activity(Activity(type="message", text=answer_text))
        return

    # STEP 2: Not a greeting -> store the answer in a dict with unique ID
    new_answer_id = str(uuid.uuid4())
    conversation_histories[answers_key][new_answer_id] = answer_text

    # STEP 3: Decide how to show the card. If "Source:" is in the answer, we do a "Show Source" toggle.
    if "\n\nSource:" in answer_text:
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
                # Export PPT
                {
                    "type": "Action.Submit",
                    "title": "Export PPT",
                    "data": {
                        "action": "export",
                        "answer_id": new_answer_id
                    }
                }
            ],
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }
    else:
        # A simple card with just "Export PPT"
        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {"type": "TextBlock", "text": answer_text, "wrap": True}
            ],
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "Export PPT",
                    "data": {
                        "action": "export",
                        "answer_id": new_answer_id
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

####################################################
# PPT Generation
####################################################
def generate_and_upload_ppt(text_for_ppt):
    """
    1) Generate a .pptx using python-pptx
    2) Upload to Azure Blob (fake placeholders remain)
    3) Return the public URL (SAS)
    """
    try:
        prs = Presentation()
        # Slide 1
        slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "Your Generated PPT"
        slide.placeholders[1].text = "Subtitle from GPT"

        # Slide 2
        bullet_slide_layout = prs.slide_layouts[1]
        slide2 = prs.slides.add_slide(bullet_slide_layout)
        slide2.shapes.title.text = "Main Answer"
        body_shape = slide2.shapes.placeholders[1]
        tf = body_shape.text_frame

        for line in text_for_ppt.split("\n"):
            if line.strip():
                p = tf.add_paragraph()
                p.text = line.strip()

        filename = f"ppt_{uuid.uuid4()}.pptx"
        prs.save(filename)

        # Upload to Azure Blob
        account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
        sas_token = (
            "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
            "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
            "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
        )
        container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        target_blob_name = f"UI/ppt_exports/{filename}"

        blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
        container_client = blob_service_client.get_container_client(container_name)

        with open(filename, "rb") as data:
            container_client.upload_blob(name=target_blob_name, data=data, overwrite=True)

        ppt_url = f"{account_url}/{container_name}/{target_blob_name}?{sas_token}"

        # Cleanup
        import os
        os.remove(filename)

        return ppt_url
    except Exception as e:
        print(f"Error generating/uploading PPT: {e}")
        return None

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
