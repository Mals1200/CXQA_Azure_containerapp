import io
import requests
import re
from pptx import Presentation
from pptx.util import Pt
from azure.storage.blob import BlobServiceClient
from datetime import datetime

def generate_ppt_from_llm(question, answer_text, chat_history_str, instructions):
    """
    1) Build an LLM prompt from question/answer/chat_history/instructions.
    2) Send the prompt to Azure OpenAI for textual slide content.
    3) Build a PPT with python-pptx.
    4) Upload the PPT to Azure Blob Storage with a SAS token.
    5) Return a link to the uploaded PPT.

    FAKE placeholders for keys and endpoints are used below.
    Replace them with your real values.
    """

    # ---------------------------------------------------------------------
    # 1) Build the LLM prompt using the format you specified:
    # ---------------------------------------------------------------------
    llm_prompt = f"""
You are a PowerPoint presentation expert. Use the following information to make the slides.
Rules:
- Only use the following information to create the slides.
- Dont come up with anything outside of your scope in your slides.
- Your output will be utilized by the "python-pptx" to create the slides.

question:
{question}

answer:
{answer_text}

Chat_History:
{chat_history_str}

User_Instructions:
{instructions}
"""

    # ---------------------------------------------------------------------
    # 2) Send the prompt to your Azure OpenAI endpoint
    # ---------------------------------------------------------------------
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }
    payload = {
        "messages": [
            {"role": "user", "content": llm_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1000,
        "stream": False
    }

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        # The LLM's textual slide content
        slides_text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        slides_text = f"Could not generate slides. Error: {str(e)}"

    # ---------------------------------------------------------------------
    # 3) Build a PPT with python-pptx using the LLM's returned text
    # ---------------------------------------------------------------------
    prs = Presentation()

    # (a) Title Slide
    title_slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(title_slide_layout)
    slide.shapes.title.text = "Auto-Generated PPT"
    slide.placeholders[1].text = "Using Azure OpenAI + python-pptx"

    # (b) Content Slide
    bullet_slide_layout = prs.slide_layouts[1]
    content_slide = prs.slides.add_slide(bullet_slide_layout)
    shapes = content_slide.shapes
    shapes.title.text = "Slide Content"
    text_frame = shapes.placeholders[1].text_frame

    intro_paragraph = text_frame.add_paragraph()
    intro_paragraph.text = "Below is the textual content from the LLM:"
    intro_paragraph.font.size = Pt(18)

    lines = slides_text.split("\n")
    for line in lines:
        line = line.strip()
        if line:
            p = text_frame.add_paragraph()
            p.text = line
            p.font.size = Pt(16)
            p.level = 1

    # ---------------------------------------------------------------------
    # 4) Upload the PPT to Azure Blob Storage (FAKE placeholders)
    # ---------------------------------------------------------------------
    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"

    ppt_stream = io.BytesIO()
    prs.save(ppt_stream)
    ppt_stream.seek(0)

    blob_name = f"gpt_ppt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"

    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(ppt_stream, overwrite=True)

    ppt_url = f"{account_url}/{container_name}/{blob_name}?{sas_token}"

    return ppt_url
