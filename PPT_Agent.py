import requests
import json
import io
import threading
from datetime import datetime
from pptx import Presentation
from pptx.util import Pt
from azure.storage.blob import BlobServiceClient

# We will import chat_history from ask_func inside the function to avoid circular imports

def Call_PPT():
    """
    1) Grabs the latest user question + answer from ask_func.chat_history.
    2) Calls Azure OpenAI (fake creds) to generate slide text.
    3) Creates a PowerPoint (.pptx) file in-memory.
    4) Uploads the file to Azure Blob Storage using the same fake credentials.
    5) Schedules auto-delete after 5 minutes.
    6) Returns a shareable SAS URL link.
    """

    # Late import to avoid circular reference
    from ask_func import chat_history

    ##################################################
    # (A) GET LATEST QUESTION + LATEST ANSWER
    ##################################################
    latest_question = ""
    latest_answer = ""

    reversed_history = list(reversed(chat_history))
    for entry in reversed_history:
        if entry.startswith("User: ") and not latest_question:
            latest_question = entry.replace("User: ", "").strip()
        elif entry.startswith("Assistant: ") and not latest_answer:
            latest_answer = entry.replace("Assistant: ", "").strip()

        if latest_question and latest_answer:
            break

    if not latest_question:
        latest_question = "No user question found."
    if not latest_answer:
        latest_answer = "No assistant answer found."

    instructions = "Make the slides short, with bullet points."

    ##################################################
    # (B) CALL AZURE OPENAI TO GET SLIDE TEXT
    ##################################################
    ppt_prompt = f"""
You are a PowerPoint presentation expert. Use the following information to make the slides.
Rules:
- Only use the following information to create the slides.
- Don't come up with anything outside of your scope in your slides.
- Your output will be utilized by the "python-pptx" to create the slides **Keep in mind for formatting**.
- Make the output complete and ready for a presentation. **Only give the text for the slides**.
- Do not add any instructions or slide_numbers with the slides text.

(The Information)
- User_Instructions:
{instructions}

- Latest_Question:
{latest_question}

- Latest_Answer:
{latest_answer}

- Full Conversation:
{chat_history}
"""
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_KEY"

    system_message = "You are a helpful assistant that formats PowerPoint slides from user input."
    payload = {
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": ppt_prompt}
        ],
        "max_tokens": 1000,
        "temperature": 0.7,
        "stream": True
    }
    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    slides_text = ""
    try:
        with requests.post(LLM_ENDPOINT, headers=headers, json=payload, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if line_str.startswith("data: "):
                        data_str = line_str[len("data: "):]
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            if (
                                "choices" in data_json
                                and data_json["choices"]
                                and "delta" in data_json["choices"][0]
                            ):
                                content_piece = data_json["choices"][0]["delta"].get("content", "")
                                slides_text += content_piece
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        return f"Error generating slides text: {e}"

    slides_text = slides_text.strip()
    if not slides_text:
        return "No valid slide text returned from Azure OpenAI."

    ##################################################
    # (C) CREATE A .PPTX IN MEMORY
    ##################################################
    raw_slides = [s.strip() for s in slides_text.split("\n\n") if s.strip()]

    prs = Presentation()
    layout = prs.slide_layouts[1]  # Title & Content

    for raw_slide in raw_slides:
        lines = raw_slide.split("\n")
        if not lines:
            continue
        title_text = lines[0]
        bullet_lines = lines[1:]

        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = title_text

        if bullet_lines:
            body_tf = slide.placeholders[1].text_frame
            body_tf.clear()
            for bullet_item in bullet_lines:
                p = body_tf.add_paragraph()
                p.text = bullet_item
                p.font.size = Pt(18)

    ppt_buffer = io.BytesIO()
    prs.save(ppt_buffer)
    ppt_buffer.seek(0)

    ##################################################
    # (D) UPLOAD TO AZURE & AUTO-DELETE AFTER 5 MIN
    ##################################################
    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=FAKE_SIGNATURE"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"

    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)

    ppt_filename = f"slides_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
    blob_client = container_client.get_blob_client(ppt_filename)

    blob_client.upload_blob(ppt_buffer, overwrite=True)
    download_link = f"{account_url}/{container_name}/{ppt_filename}?{sas_token}"

    def delete_blob_after_5():
        try:
            blob_client.delete_blob()
        except Exception:
            pass

    timer = threading.Timer(300, delete_blob_after_5)  # 5 minutes = 300 seconds
    timer.start()

    return download_link
