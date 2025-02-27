# ppt_export_agent.py
import os
import uuid
import requests
import json
from pptx import Presentation
from azure.storage.blob import BlobServiceClient

############################################################################
# Credentials for Azure OpenAI
############################################################################
LLM_DEPLOYMENT_NAME = "gpt-4o"
LLM_ENDPOINT = (
    "https://cxqaazureaihub2358016269.openai.azure.com/"
    "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
)
LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

############################################################################
# Credentials for Azure Blob
############################################################################
ACCOUNT_URL = "https://cxqaazureaihub8779474245.blob.core.windows.net"
SAS_TOKEN = (
    "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
    "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
    "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
)
CONTAINER_NAME = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"

def generate_ppt_from_llm(question: str,
                          answer_text: str,
                          chat_history_str: str,
                          instructions: str = "") -> str:
    """
    1) Calls GPT with a built-in prompt referencing question, answer, chat_history, instructions.
    2) GPT returns text, which we treat as bullet points.
    3) Builds PPT. 4) Uploads to Azure Blob. 5) Returns SAS URL or "" on error.
    """
    try:
        ####################################################################
        # 1) Build the prompt & call GPT
        ####################################################################
        system_prompt = (
            "You are a helpful AI specialized in creating bullet points for a PowerPoint presentation. "
            "Focus on clarity and brevity."
        )
        user_prompt = f"""
Based on the Question, chat_history, and instructions make a PowerPoint presentation.

Instructions:
{instructions}

Chat_History:
{chat_history_str}

Question:
{question}

Answer:
{answer_text}
"""

        headers = {
            "Content-Type": "application/json",
            "api-key": LLM_API_KEY
        }
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 800,
            "temperature": 0.7
        }

        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        gpt_text = data["choices"][0]["message"]["content"].strip()

        ####################################################################
        # 2) Build the PPT from GPT text
        ####################################################################
        prs = Presentation()
        # Slide 1: Title & Subtitle
        slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "Generated PPT from GPT"
        if len(question) > 70:
            question_display = question[:70] + "..."
        else:
            question_display = question
        slide.placeholders[1].text = f"Question: {question_display}"

        # Slide 2: bullet points from GPT
        bullet_layout = prs.slide_layouts[1]
        slide2 = prs.slides.add_slide(bullet_layout)
        slide2.shapes.title.text = "GPT Slide Content"
        body_shape = slide2.shapes.placeholders[1]
        text_frame = body_shape.text_frame

        # Convert GPT text to bullet points
        for line in gpt_text.split("\n"):
            clean_line = line.strip()
            if clean_line:
                p = text_frame.add_paragraph()
                p.text = clean_line

        filename = f"ppt_{uuid.uuid4()}.pptx"
        prs.save(filename)

        ####################################################################
        # 3) Upload to Azure Blob (fake placeholders)
        ####################################################################
        blob_service_client = BlobServiceClient(
            account_url=ACCOUNT_URL,
            credential=SAS_TOKEN
        )
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)

        target_blob_name = f"UI/ppt_exports/{filename}"
        with open(filename, "rb") as f:
            container_client.upload_blob(name=target_blob_name, data=f, overwrite=True)

        ppt_url = f"{ACCOUNT_URL}/{CONTAINER_NAME}/{target_blob_name}?{SAS_TOKEN}"

        # Cleanup
        import os
        os.remove(filename)

        return ppt_url

    except Exception as ex:
        print("Error in generate_ppt_from_llm:", ex)
        return ""
