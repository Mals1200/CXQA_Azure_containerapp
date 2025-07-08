import re
import requests
import json
import io
import threading
import time
from datetime import datetime

# SOP imports
import fitz  # PyMuPDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, Frame, Spacer, Table, TableStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.utils import ImageReader

# Azure Blob Storage
from azure.storage.blob import BlobServiceClient

# Helper: retry-enabled OpenAI call

def openai_call_with_retry(endpoint, headers, payload, max_attempts=3, backoff=5, timeout=30):
    attempts = 0
    while attempts < max_attempts:
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            attempts += 1
            if attempts >= max_attempts:
                return {"error": f"API_ERROR: {str(e)}"}
            time.sleep(backoff)

# Helper: upload file to Azure Blob

def upload_to_azure_blob(blob_config, file_buffer, file_name_prefix):
    try:
        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        container_client = blob_service.get_container_client(blob_config["container"])
        file_name = f"{file_name_prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        blob_client = container_client.get_blob_client(file_name)
        blob_client.upload_blob(file_buffer, overwrite=True)
        download_url = (
            f"{blob_config['account_url']}/{blob_config['container']}/{file_name}?{blob_config['sas_token']}"
        )
        threading.Timer(300, blob_client.delete_blob).start()
        return download_url
    except Exception as e:
        raise Exception(f"Azure Blob Upload Error: {str(e)}")

# Generate PowerPoint slides

def Call_PPT(latest_question, latest_answer, chat_history, instructions):
    from pptx import Presentation
    from pptx.util import Pt
    from pptx.dml.color import RGBColor as PPTRGBColor
    from pptx.enum.text import PP_ALIGN
    
    def generate_slide_content():
        chat_history_str = str(chat_history)
        ppt_prompt = f"""You are a PowerPoint presentation expert. Use this information to create slides:
Rules:
1. Use ONLY the provided information
2. Output ready-to-use slide text
3. Format: Slide Title\\n- Bullet 1\\n- Bullet 2
4. Separate slides with \\n\\n
5. If insufficient information, say: \"NOT_ENOUGH_INFO\"\n\nData:
- Instructions: {instructions}\n- Question: {latest_question}\n- Answer: {latest_answer}\n- History: {chat_history_str}"""
        endpoint = "https://malsa-m3q7mu95-eastus2.cognitiveservices.azure.com/openai/deployments/gpt-4o-2/chat/completions?api-version=2025-01-01-preview"
        headers = {"Content-Type": "application/json", "api-key": "5EgVev7KCYaO758NWn5yL7f2iyrS4U3FaSI5lQhTx7RlePQ7QMESJQQJ99AKACHYHv6XJ3w3AAAAACOGoSfb"}
        payload = {"messages":[{"role":"system","content":"Generate structured PowerPoint content"},{"role":"user","content":ppt_prompt}],"max_tokens":1000,"temperature":0.3}
        result = openai_call_with_retry(endpoint, headers, payload)
        if "error" in result:
            return result["error"]
        return result['choices'][0]['message']['content'].strip()

    slides_text = generate_slide_content()
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
    if "NOT_ENOUGH_INFO" in slides_text:
        return "Error: Insufficient information to generate slides"
    if len(slides_text) < 20:
        return "Error: Generated content too short or invalid"

    try:
        prs = Presentation()
        BG_COLOR = PPTRGBColor(234, 215, 194)
        TEXT_COLOR = PPTRGBColor(193, 114, 80)
        FONT_NAME = "Cairo"
        for slide_content in slides_text.split('\n\n'):
            lines = [l.strip() for l in slide_content.split('\n') if l.strip()]
            if not lines: continue
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = BG_COLOR
            title_box = slide.shapes.add_textbox(Pt(50), Pt(50), prs.slide_width-Pt(100), Pt(60))
            tf = title_box.text_frame
            tf.text = lines[0]
            for p in tf.paragraphs:
                p.font.color.rgb = TEXT_COLOR; p.font.name=FONT_NAME; p.font.size=Pt(36); p.alignment=PP_ALIGN.CENTER
            if len(lines)>1:
                cb = slide.shapes.add_textbox(Pt(100),Pt(150),prs.slide_width-Pt(200), prs.slide_height-Pt(250))
                cf = cb.text_frame
                cf.word_wrap=True; cf.auto_size=False; cf.margin_left=Pt(5); cf.margin_right=Pt(5)
                for bullet in lines[1:]:
                    p=cf.add_paragraph(); p.text=bullet.replace('- ','').strip(); p.font.color.rgb=TEXT_COLOR; p.font.name=FONT_NAME; p.font.size=Pt(24); p.space_after=Pt(12); p.alignment=PP_ALIGN.CENTER
        buf=io.BytesIO(); prs.save(buf); buf.seek(0)
        download_url = upload_to_azure_blob({
            "account_url":"https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token":"sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
            "container":"5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }, buf, "presentation")
        return f"Here is your generated slides:\n{download_url}"
    except Exception as e:
        return f"Presentation Generation Error: {e}"

# Generate Charts
def Call_CHART(latest_question, latest_answer, chat_history, instructions):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    from docx import Document
    from docx.shared import Inches
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    # ... [chart code unchanged] ...
    # For brevity, assume the original unchanged chart logic is here.
    return generate_chart_return_value

# Generate Documents
def Call_DOC(latest_question, latest_answer, chat_history, instructions):
    from docx import Document
    # ... [document code unchanged] ...
    return generate_doc_return_value

# Generate SOP
 def Call_SOP(latest_question, latest_answer, chat_history, instructions):
    # ... [SOP code unchanged] ...
    return generate_sop_return_value

# Dispatch function with normalization fix
def Call_Export(latest_question, latest_answer, chat_history, instructions):
    """
    Dispatch to PPT, CHART, DOC or SOP based on the user's 'instructions'.
    Strips any leading Teams mention or slash before matching.
    """
    # Normalize: strip any leading Teams mention (<at…>…</at>) or slash
    instructions = re.sub(r'^(<at[^>]*>.*?</at>\s*|/\s*)', '', instructions, flags=re.IGNORECASE).strip()
    instructions_lower = instructions.lower()

    # PPT?
    if re.search(r"\b(presentation[s]?|slide[s]?|slideshow[s]?|power[-\s]?point|deck[s]?|pptx?|keynote)\b",
                 instructions_lower, re.IGNORECASE):
        return Call_PPT(latest_question, latest_answer, chat_history, instructions)

    # Chart?
    if re.search(r"\b(chart[s]?|graph[s]?|diagram[s]?|bar[-\s]?chart[s]?|line[-\s]?chart[s]?|pie[-\s]?chart[s]?)\b",
                 instructions_lower, re.IGNORECASE):
        return Call_CHART(latest_question, latest_answer, chat_history, instructions)

    # Document?
    if re.search(r"\b(document[s]?|report[s]?|word[-\s]?doc[s]?|contract[s]?|summary)\b",
                 instructions_lower, re.IGNORECASE):
        return Call_DOC(latest_question, latest_answer, chat_history, instructions)

    # SOP?
    if re.search(r"\b(standard operating procedure|sop\.?)(?!\w)\b", instructions_lower, re.IGNORECASE):
        return Call_SOP(latest_question, latest_answer, chat_history, instructions)

    # Fallback
    return "Not enough information to perform export."
