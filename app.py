import os
import asyncio
from flask import Flask, request, jsonify, Response
import requests

# Bot Framework imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity, Attachment

# Import your existing logic from ask_func.py
from ask_func import Ask_Question, chat_history

#####################################################
#       Additional Libraries for PPT Generation     #
#####################################################
from pptx import Presentation
from pptx.util import Inches
import uuid

# Azure Blob Storage for uploading PPT
from azure.storage.blob import BlobServiceClient

app = Flask(__name__)

# Read Bot credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create settings & adapter for the Bot
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Global dictionary to maintain conversation-specific chat histories
# AND store the last GPT answer for potential PPT export.
conversation_histories = {}
# e.g. conversation_histories[conversation_id] = [ ... chat history ... ]
#      conversation_histories[conversation_id + "_last_answer"] = "some text"

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    question = data['question']
    # For direct usage, we still rely on the global ask_func chat_history:
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)
    # Deserialize incoming Activity
    body = request.json
    activity = Activity().deserialize(body)
    # Get the Authorization header (for Bot Framework auth)
    auth_header = request.headers.get("Authorization", "")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()
    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    """
    Main logic to handle user messages.
    1) If it's a normal text message, we get an answer from Ask_Question.
    2) If the user clicked the "Export PPT" button, handle that event.
    """
    # Retrieve conversation ID
    conversation_id = turn_context.activity.conversation.id

    # Initialize conversation history for this conversation if it doesn't exist
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    
    # Also set up a key for storing the last GPT answer
    last_answer_key = conversation_id + "_last_answer"
    if last_answer_key not in conversation_histories:
        conversation_histories[last_answer_key] = ""

    # Pull in ask_func's chat_history so it matches this conversation's history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # Distinguish between a normal user message vs. an Action.Submit from the Adaptive Card
    if turn_context.activity.value and "action" in turn_context.activity.value:
        # The user clicked a button that sent an Action.Submit
        action = turn_context.activity.value["action"]
        if action == "export":
            # 1) Retrieve the last GPT answer from the conversation state
            text_for_ppt = conversation_histories.get(last_answer_key, "")
            if not text_for_ppt:
                await turn_context.send_activity("No previous answer found to export.")
                return

            # 2) Generate a PPT from that text
            ppt_filename = generate_and_upload_ppt(text_for_ppt)
            if ppt_filename:
                # 3) Provide a link or message that the PPT is available
                await turn_context.send_activity(
                    f"I've generated your PPT! You can download it here:\n{ppt_filename}"
                )
            else:
                await turn_context.send_activity("Sorry, I couldn't create the PPT file.")
        else:
            await turn_context.send_activity("Unknown action received.")
        return

    # Otherwise, it's a normal user text message
    user_message = turn_context.activity.text or ""
    answer = Ask_Question(user_message)

    # Update conversation-specific history after calling the ask_func
    conversation_histories[conversation_id] = ask_func.chat_history
    # Also store the last GPT answer for potential PPT export
    conversation_histories[last_answer_key] = answer

    # Now check if the answer has "Source:" content
    if "\n\nSource:" in answer:
        parts = answer.split("\n\nSource:", 1)
        main_answer = parts[0].strip()
        source_details = "Source:" + parts[1].strip()

        # Build an Adaptive Card with toggle + export
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
                # Our new Export PPT button
                {
                    "type": "Action.Submit",
                    "title": "Export PPT",
                    "data": {"action": "export"}
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
        # Plain text message if no "Source:" in the answer
        await turn_context.send_activity(Activity(type="message", text=answer))

###########################################
#       PPT Generation Helper            #
###########################################
def generate_and_upload_ppt(text_for_ppt):
    """
    1) Generate a local PPTX file from the answer text.
    2) Upload it to Azure Blob Storage (or any location).
    3) Return a direct link or None if error.
    """

    try:
        # 1) Generate PPT using python-pptx
        #    (Basic example: two slides - title + bullet points)
        prs = Presentation()
        # Slide 1: Title & Subtitle
        slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "Your Generated PPT"
        slide.placeholders[1].text = "Subtitle from GPT"

        # Slide 2: bullet points
        bullet_slide_layout = prs.slide_layouts[1]
        slide2 = prs.slides.add_slide(bullet_slide_layout)
        slide2.shapes.title.text = "Main Answer"
        body_shape = slide2.shapes.placeholders[1]
        tf = body_shape.text_frame

        for line in text_for_ppt.split("\n"):
            if line.strip():
                p = tf.add_paragraph()
                p.text = line.strip()

        # Save to a temporary file
        import uuid
        temp_filename = f"ppt_{uuid.uuid4()}.pptx"
        prs.save(temp_filename)

        # 2) Upload to Azure Blob and return a link
        #    REPLACE placeholders with your actual storage info if needed.
        account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
        sas_token = (
            "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
            "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
            "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
        )
        container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        target_blob_name = f"UI/ppt_exports/{temp_filename}"

        blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
        container_client = blob_service_client.get_container_client(container_name)

        with open(temp_filename, "rb") as data:
            container_client.upload_blob(name=target_blob_name, data=data, overwrite=True)

        ppt_url = f"{account_url}/{container_name}/{target_blob_name}?{sas_token}"

        # Optionally remove local file
        import os
        os.remove(temp_filename)

        return ppt_url
    except Exception as e:
        print(f"Error generating/uploading PPT: {e}")
        return None

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
