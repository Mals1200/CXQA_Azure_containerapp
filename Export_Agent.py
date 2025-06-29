# version 5

import re
import requests
import json
import io
import threading
import time
import logging
from datetime import datetime

# SOP and document/PDF imports
import fitz  # PyMuPDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Paragraph, Spacer, SimpleDocTemplate
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.utils import ImageReader

# Azure Blob
from azure.storage.blob import BlobServiceClient

# ============ CONFIG ===============
AZURE_BLOB_CONFIG = {
    "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
    "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
}
GPT_ENDPOINT = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview"
GPT_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

# ========== Utility Functions ==========

def openai_call_with_retry(endpoint, headers, payload, max_attempts=3, backoff=5, timeout=30):
    for attempt in range(max_attempts):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt + 1 == max_attempts:
                return {"error": f"API_ERROR: {str(e)}"}
            time.sleep(backoff)

def upload_to_azure_blob(blob_config, file_buffer, file_name_prefix):
    try:
        blob_service = BlobServiceClient(account_url=blob_config["account_url"], credential=blob_config["sas_token"])
        container_client = blob_service.get_container_client(blob_config["container"])
        file_name = f"{file_name_prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        blob_client = container_client.get_blob_client(file_name)
        blob_client.upload_blob(file_buffer, overwrite=True)
        download_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{file_name}?"
            f"{blob_config['sas_token']}"
        )
        threading.Timer(300, lambda: safe_blob_delete(blob_client, file_name)).start()
        return download_url
    except Exception as e:
        logging.exception(f"Azure Blob Upload Error: {str(e)}")
        raise Exception(f"Azure Blob Upload Error: {str(e)}")

def safe_blob_delete(blob_client, blob_name):
    try:
        blob_client.delete_blob()
        logging.info(f"Deleted blob: {blob_name}")
    except Exception as e:
        logging.warning(f"Failed to delete blob '{blob_name}': {str(e)}")

def safe_json_loads(raw):
    try:
        return json.loads(raw)
    except Exception as e:
        return None

# ========== PowerPoint Export ==========
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
5. If insufficient information, say: "NOT_ENOUGH_INFO"

Data:
- Instructions: {instructions}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""
        headers = {"Content-Type": "application/json", "api-key": GPT_KEY}
        payload = {
            "messages": [
                {"role": "system", "content": "Generate structured PowerPoint content"},
                {"role": "user", "content": ppt_prompt}
            ],
            "max_tokens": 1000, "temperature": 0.3
        }
        result_json = openai_call_with_retry(GPT_ENDPOINT, headers, payload)
        if "error" in result_json:
            return result_json["error"]
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    slides_text = generate_slide_content()
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
    if "NOT_ENOUGH_INFO" in slides_text or len(slides_text) < 20:
        return "Error: Insufficient information to generate slides"

    try:
        prs = Presentation()
        BG_COLOR = PPTRGBColor(234, 215, 194)
        TEXT_COLOR = PPTRGBColor(193, 114, 80)
        FONT_NAME = "Cairo"
        for slide_content in slides_text.split('\n\n'):
            lines = [line.strip() for line in slide_content.split('\n') if line.strip()]
            if not lines: continue
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = BG_COLOR
            # Title
            title_box = slide.shapes.add_textbox(Pt(50), Pt(50), prs.slide_width - Pt(100), Pt(60))
            title_frame = title_box.text_frame
            title_frame.text = lines[0]
            for paragraph in title_frame.paragraphs:
                paragraph.font.color.rgb = TEXT_COLOR
                paragraph.font.name = FONT_NAME
                paragraph.font.size = Pt(36)
                paragraph.alignment = PP_ALIGN.CENTER
            # Bullets
            if len(lines) > 1:
                content_box = slide.shapes.add_textbox(Pt(100), Pt(150), prs.slide_width - Pt(200), prs.slide_height - Pt(250))
                content_frame = content_box.text_frame
                for bullet in lines[1:]:
                    p = content_frame.add_paragraph()
                    p.text = bullet.replace('- ', '').strip()
                    p.font.color.rgb = TEXT_COLOR
                    p.font.name = FONT_NAME
                    p.font.size = Pt(24)
                    p.space_after = Pt(12)
        ppt_buffer = io.BytesIO()
        prs.save(ppt_buffer)
        ppt_buffer.seek(0)
        download_url = upload_to_azure_blob(AZURE_BLOB_CONFIG, ppt_buffer, f"presentation_{datetime.now().strftime('%Y%m%d%H%M%S')}.pptx")
        return f"Here is your generated slides:\n{download_url}"
    except Exception as e:
        logging.exception("PPT Generation Error")
        return f"Presentation Generation Error: {str(e)}"

# ========== Chart Export ==========
def Call_CHART(latest_question, latest_answer, chat_history, instructions):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    from docx import Document
    from docx.shared import Inches
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

    CHART_COLORS = [
        (193/255, 114/255, 80/255),
        (85/255, 20/255, 45/255),
        (219/255, 188/255, 154/255),
        (39/255, 71/255, 54/255),
        (254/255, 200/255, 65/255)
    ]

    def generate_chart_data():
        chat_history_str = str(chat_history)
        chart_prompt = f"""You are a converter that outputs ONLY valid JSON.
Do not include any explanations, code fences, or additional text.
Either return exactly one valid JSON object like:
{{
  "chart_type": "bar"|"line"|"column",
  "title": "string",
  "categories": ["Category1","Category2",...],
  "series": [
    {{"name": "Series1", "values": [num1, num2, ...]}},
    ...
  ]
}}
OR return the EXACT string:
"Information is not suitable for a chart"
Nothing else.
Data:
- Instructions: {instructions}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""
        headers = {"Content-Type": "application/json", "api-key": GPT_KEY}
        payload = {
            "messages": [
                {"role": "system", "content": "Output ONLY valid JSON as described."},
                {"role": "user", "content": chart_prompt}
            ],
            "max_tokens": 1000, "temperature": 0.3
        }
        result_json = openai_call_with_retry(GPT_ENDPOINT, headers, payload)
        if "error" in result_json:
            return result_json["error"]
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    def create_chart_image(chart_data):
        try:
            plt.rcParams['axes.titleweight'] = 'bold'
            plt.rcParams['axes.titlesize'] = 12
            if not all(k in chart_data for k in ['chart_type', 'title', 'categories', 'series']):
                raise ValueError("Missing keys in chart data.")
            fig, ax = plt.subplots(figsize=(8, 4.5))
            color_cycle = CHART_COLORS
            for idx, series in enumerate(chart_data['series']):
                color = color_cycle[idx % len(color_cycle)]
                if chart_data['chart_type'] in ['bar', 'column']:
                    ax.bar(chart_data['categories'], series['values'], label=series['name'], color=color, width=0.6)
                else:
                    ax.plot(chart_data['categories'], series['values'], label=series['name'], color=color, marker='o', linewidth=2.5)
            ax.set_title(chart_data['title'])
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            plt.xticks(rotation=45, ha='right')
            plt.legend()
            plt.tight_layout()
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', dpi=150)
            img_buffer.seek(0)
            plt.close()
            return img_buffer
        except Exception as e:
            logging.exception("Chart Error")
            return None

    try:
        chart_response = generate_chart_data()
        if chart_response.startswith("API_ERROR:"):
            return f"OpenAI Error: {chart_response[10:]}"
        if chart_response.strip() == "Information is not suitable for a chart":
            return "Information is not suitable for a chart"
        match = re.search(r'(\{.*\})', chart_response, re.DOTALL)
        if not match:
            return "Invalid chart data format: No JSON object found"
        try:
            chart_data = json.loads(match.group(1))
            if not all(k in chart_data for k in ['chart_type', 'title', 'categories', 'series']):
                return "Invalid chart data format: Missing keys"
        except Exception as e:
            return f"Invalid chart data format: {str(e)}"
        img_buffer = create_chart_image(chart_data)
        if not img_buffer:
            return "Failed to generate chart from data"
        doc = Document()
        doc.add_heading(chart_data['title'], level=1)
        doc.add_picture(img_buffer, width=Inches(6))
        para = doc.add_paragraph("Source: Generated from provided data")
        para.alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT
        doc_buffer = io.BytesIO()
        doc.save(doc_buffer)
        doc_buffer.seek(0)
        download_url = upload_to_azure_blob(AZURE_BLOB_CONFIG, doc_buffer, f"chart_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx")
        return f"Here is your generated chart:\n{download_url}"
    except Exception as e:
        logging.exception("Chart Generation Error")
        return f"Chart Generation Error: {str(e)}"

# ========== Document Export ==========
def Call_DOC(latest_question, latest_answer, chat_history, instructions_doc):
    from docx import Document
    from docx.shared import Pt as DocxPt, Inches, RGBColor as DocxRGBColor
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml

    def generate_doc_content():
        chat_history_str = str(chat_history)
        doc_prompt = f"""You are a professional document writer. Use this information to create content:
Rules:
1. Use ONLY the provided information
2. Output ready-to-use document text
3. Format: 
   Section Heading\\n- Bullet 1\\n- Bullet 2
4. Separate sections with \\n\\n
5. If insufficient information, say: "Not enough Information to perform export."
Data:
- Instructions: {instructions_doc}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""
        headers = {"Content-Type": "application/json", "api-key": GPT_KEY}
        payload = {
            "messages": [
                {"role": "system", "content": "Generate structured document content"},
                {"role": "user", "content": doc_prompt}
            ],
            "max_tokens": 1000, "temperature": 0.3
        }
        result_json = openai_call_with_retry(GPT_ENDPOINT, headers, payload)
        if "error" in result_json:
            return result_json["error"]
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    doc_text = generate_doc_content()
    if doc_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {doc_text[10:]}"
    if "NOT_ENOUGH_INFO" in doc_text.upper() or len(doc_text) < 20:
        return "Error: Insufficient information to generate document"

    try:
        doc = Document()
        BG_COLOR_HEX = "EAD7C2"
        TITLE_COLOR = DocxRGBColor(193, 114, 80)
        BODY_COLOR = DocxRGBColor(0, 0, 0)
        FONT_NAME = "Cairo"
        TITLE_SIZE = DocxPt(16)
        BODY_SIZE = DocxPt(12)
        style = doc.styles['Normal']
        style.font.name = FONT_NAME
        style.font.size = BODY_SIZE
        style.font.color.rgb = BODY_COLOR
        for section in doc.sections:
            sectPr = section._sectPr
            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{BG_COLOR_HEX}"/>')
            sectPr.append(shd)
        for section_content in doc_text.split('\n\n'):
            lines = [line.strip() for line in section_content.split('\n') if line.strip()]
            if not lines: continue
            heading = doc.add_heading(level=1)
            heading.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            heading_run = heading.add_run(lines[0])
            heading_run.font.color.rgb = TITLE_COLOR
            heading_run.font.size = TITLE_SIZE
            heading_run.bold = True
            if len(lines) > 1:
                for bullet in lines[1:]:
                    para = doc.add_paragraph(style='ListBullet')
                    run = para.add_run(bullet.replace('- ', '').strip())
                    run.font.color.rgb = BODY_COLOR
            doc.add_paragraph()
        doc_buffer = io.BytesIO()
        doc.save(doc_buffer)
        doc_buffer.seek(0)
        download_url = upload_to_azure_blob(AZURE_BLOB_CONFIG, doc_buffer, f"document_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx")
        return f"Here is your generated Document:\n{download_url}"
    except Exception as e:
        logging.exception("Document Generation Error")
        return f"Document Generation Error: {str(e)}"

# ========== SOP Export ==========
def Call_SOP(latest_question, latest_answer, chat_history, instructions):
    def generate_sop_content():
        chat_history_str = str(chat_history)
        sop_prompt = f"""
You are an SOP writer. Based on the Provided Information, produce only JSON object with fields and nothing else:

The structure:
- title
- table_of_contents
- overview
- scope
- policy
- provisions
- definitions
- process_responsibilities
- process
- procedures
- related_docs
- sop_form
- sop_log

Return **only** valid JSON (no extra text).

IMPORTANT RULES:
1. No triple backticks or code fences.
2. No explanations
3. No extra characters 
4. Return ONLY the JSON object.

The Information to use:
Conversation:
{chat_history}

User_request:
{latest_question}

Final_answer_to_the_user:
{latest_answer}

User_description:
{instructions}
"""
        headers = {"Content-Type": "application/json", "api-key": GPT_KEY}
        payload = {
            "messages": [
                {"role": "system", "content": "Generate SOP content in a structured manner."},
                {"role": "user", "content": sop_prompt}
            ],
            "max_tokens": 1000, "temperature": 0.3
        }
        result_json = openai_call_with_retry(GPT_ENDPOINT, headers, payload)
        if "error" in result_json:
            return f"API_ERROR: {result_json['error']}"
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    raw_json = generate_sop_content()
    if raw_json.startswith("API_ERROR:"):
        return f"OpenAI API Error: {raw_json[10:]}"
    if "NOT_ENOUGH_INFO" in raw_json.upper() or len(raw_json) < 20:
        return "Error: Insufficient information to generate SOP"
    sop_data = safe_json_loads(raw_json)
    if not sop_data:
        return f"Error: GPT output wasn't valid JSON. Raw: {raw_json}"
    # Extract fields with .get for safety
    sop_title = sop_data.get("title", "Untitled SOP")
    toc_text = sop_data.get("table_of_contents", "")
    overview_text = sop_data.get("overview", "")
    scope_text = sop_data.get("scope", "")
    policy_text = sop_data.get("policy", "")
    provisions_data = sop_data.get("provisions", "")
    definitions_data = sop_data.get("definitions", "")
    proc_resp_data = sop_data.get("process_responsibilities", "")
    process_text = sop_data.get("process", "")
    procedures_data = sop_data.get("procedures", "")
    related_docs = sop_data.get("related_docs", "")
    sop_form = sop_data.get("sop_form", "")
    sop_log = sop_data.get("sop_log", "")

    try:
        buffer_front = io.BytesIO()
        c = canvas.Canvas(buffer_front, pagesize=A4)
        page_width, page_height = A4

        def fetch_image(img_name):
            blob_service = BlobServiceClient(
                account_url=AZURE_BLOB_CONFIG["account_url"],
                credential=AZURE_BLOB_CONFIG["sas_token"]
            )
            container_client = blob_service.get_container_client(AZURE_BLOB_CONFIG["container"])
            blob_client = container_client.get_blob_client(img_name)
            img_data = io.BytesIO()
            try:
                blob_client.download_blob().readinto(img_data)
                img_data.seek(0)
                return img_data
            except Exception:
                return None

        try:
            logo_img = ImageReader(fetch_image("UI/2024-11-20_142337_UTC/cxqa_data/export-resources/logo.png"))
            art_img = ImageReader(fetch_image("UI/2024-11-20_142337_UTC/cxqa_data/export-resources/art.png"))
        except Exception:
            logo_img = None
            art_img = None

        # FRONT PAGE
        if logo_img:
            logo_width = 70
            c.drawImage(
                logo_img,
                (page_width - logo_width) / 2,
                page_height - 150,
                width=logo_width,
                preserveAspectRatio=True,
                mask='auto'
            )
        if art_img:
            original_width, original_height = art_img.getSize()
            ratio = 0.8
            scaled_width = original_width * ratio
            scaled_height = original_height * ratio
            x_pos = (page_width - scaled_width) / 2
            y_pos = 0
            c.drawImage(
                art_img,
                x=x_pos,
                y=y_pos,
                width=scaled_width,
                height=scaled_height,
                preserveAspectRatio=False,
                mask='auto'
            )
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(colors.black)
        c.drawCentredString(page_width/2, page_height - 230, sop_title)
        c.setFont("Helvetica", 12)
        c.setFillColor(colors.HexColor("#777777"))
        c.drawCentredString(page_width/2, page_height - 250, "Standard Operating Procedure Document")
        c.setFont("Helvetica", 10)
        meta_y = page_height - 310
        meta_lines = [
            f"Document Name: {sop_title}",
            f"Approved Date: {datetime.today().strftime('%B %d, %Y')}",
            "Version: 001",
            "Document Prepared By: Standards & Delivery"
        ]
        for line in meta_lines:
            c.drawString(50, meta_y, line)
            meta_y -= 12
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.black)
        c.drawString(40, 20, "ClassificationRedacted")
        c.showPage()

        style_heading = ParagraphStyle('heading', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor("#C17250"), spaceAfter=6)
        style_text = ParagraphStyle('text', fontName='Helvetica', fontSize=10, leading=14, spaceAfter=10)

        def add_section(title, content, story):
            if not content: return
            story.append(Paragraph(title, style_heading))
            if isinstance(content, list):
                for item in content:
                    story.append(Paragraph(f"- {item}", style_text))
            elif isinstance(content, dict):
                for k, v in content.items():
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, indent=2)
                    story.append(Paragraph(f"{k}: {v}", style_text))
            else:
                lines = str(content).split("\n")
                for line in lines:
                    line = line.strip()
                    if line:
                        story.append(Paragraph(line, style_text))
            story.append(Spacer(1, 12))

        buffer_content = io.BytesIO()
        doc_story = []
        add_section("Table of Contents", toc_text, doc_story)
        add_section("1 Overview", overview_text, doc_story)
        add_section("1.1 Scope", scope_text, doc_story)
        add_section("2 Policy and References", policy_text, doc_story)
        add_section("3 General Provisions", provisions_data, doc_story)
        add_section("4 Terms and Definitions", definitions_data, doc_story)
        add_section("5 Process and Responsibilities", proc_resp_data, doc_story)
        add_section(f"5.1 {sop_title} Process", process_text, doc_story)
        add_section("6 Procedures", procedures_data, doc_story)
        add_section("7 Related Documents and Records", related_docs, doc_story)
        add_section(f"7.1 {sop_title} Form", sop_form, doc_story)
        add_section(f"7.2 {sop_title} Log", sop_log, doc_story)
        doc = SimpleDocTemplate(buffer_content, pagesize=A4)
        doc.build(doc_story)
        c.save()
        buffer_content.seek(0)
        front_pdf = fitz.open(stream=buffer_front.getvalue(), filetype="pdf")
        content_pdf = fitz.open(stream=buffer_content.getvalue(), filetype="pdf")
        front_pdf.insert_pdf(content_pdf)
        final_output = io.BytesIO()
        front_pdf.save(final_output)
        final_output.seek(0)
        blob_service = BlobServiceClient(
            account_url=AZURE_BLOB_CONFIG["account_url"],
            credential=AZURE_BLOB_CONFIG["sas_token"]
        )
        container_client = blob_service.get_container_client(AZURE_BLOB_CONFIG["container"])
        blob_name = f"sop_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(final_output, overwrite=True)
        final_url = (
            f"{AZURE_BLOB_CONFIG['account_url']}/"
            f"{AZURE_BLOB_CONFIG['container']}/"
            f"{blob_name}?"
            f"{AZURE_BLOB_CONFIG['sas_token']}"
        )
        threading.Timer(300, lambda: safe_blob_delete(blob_client, blob_name)).start()
        return f"Here is your generated SOP:\n{final_url}"
    except Exception as e:
        logging.exception("SOP Generation Error")
        return f"SOP Generation Error: {str(e)}"

# ========== Export Agent Entrypoint ==========
def Call_Export(latest_question, latest_answer, chat_history, instructions):
    if not isinstance(latest_answer, str) or len(latest_answer.strip()) < 20:
        return "Cannot export: there is not enough information in the last answer to generate a document or slides."
    instructions_lower = (instructions or "").lower().strip()
    if not instructions_lower:
        return "Cannot export: instructions or format were not specified."
    # Robust matching for type
    if re.search(
        r"\b("
        r"presentation[s]?|slide[s]?|slideshow[s]?|"
        r"power[-\s]?point|deck[s]?|pptx?|keynote|"
        r"pitch[-\s]?deck|talk[-\s]?deck|slide[-\s]?deck|"
        r"seminar|webinar|conference[-\s]?slides|training[-\s]?materials|"
        r"meeting[-\s]?slides|workshop[-\s]?slides|lecture[-\s]?slides|"
        r"presenation|presentaion"
        r")\b", instructions_lower, re.IGNORECASE
    ):
        return Call_PPT(latest_question, latest_answer, chat_history, instructions)
    elif re.search(
        r"\b("
        r"chart[s]?|graph[s]?|diagram[s]?|"
        r"bar[-\s]?chart[s]?|line[-\s]?chart[s]?|pie[-\s]?chart[s]?|"
        r"scatter[-\s]?plot[s]?|trend[-\s]?analysis|visualization[s]?|"
        r"infographic[s]?|data[-\s]?graph[s]?|report[-\s]?chart[s]?|"
        r"heatmap[s]?|time[-\s]?series|distribution[-\s]?plot|"
        r"statistical[-\s]?graph[s]?|data[-\s]?plot[s]?|"
        r"char|grph|daigram"
        r")\b", instructions_lower, re.IGNORECASE
    ):
        return Call_CHART(latest_question, latest_answer, chat_history, instructions)
    elif re.search(
        r"\b("
        r"document[s]?|report[s]?|word[-\s]?doc[s]?|"
        r"policy[-\s]?paper[s]?|manual[s]?|write[-\s]?up[s]?|"
        r"summary|white[-\s]?paper[s]?|memo[s]?|contract[s]?|"
        r"business[-\s]?plan[s]?|research[-\s]?paper[s]?|"
        r"proposal[s]?|guideline[s]?|introduction|conclusion|"
        r"terms[-\s]?of[-\s]?service|agreement|"
        r"contract[-\s]?draft|standard[-\s]?operating[-\s]?procedure|"
        r"documnt|repot|worddoc|proposel"
        r")\b", instructions_lower, re.IGNORECASE
    ):
        return Call_DOC(latest_question, latest_answer, chat_history, instructions)
    elif re.search(r"\b(standard operating procedure document|standard operating procedure|sop\.?)\b", instructions_lower, re.IGNORECASE):
        return Call_SOP(latest_question, latest_answer, chat_history, instructions)
    return "Cannot export: format not recognized or supported."

