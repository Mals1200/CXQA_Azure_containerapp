import re
import requests
import json
import io
import threading
from datetime import datetime
from azure.storage.blob import BlobServiceClient

##################################################
# Generate PowerPoint
##################################################
def Call_PPT(latest_question, latest_answer, chat_history, instructions):
    from pptx import Presentation
    from pptx.util import Pt
    from pptx.dml.color import RGBColor as PPTRGBColor
    from pptx.enum.text import PP_ALIGN

    def generate_slide_content():
        chat_history_str = str(chat_history)
        ppt_prompt = f"""You are a PowerPoint presentation expert. Use the info below to create slides:
Rules:
1. Output slides in text form: 
   Slide Title\\n- Bullet 1\\n- Bullet 2
2. Separate slides with \\n\\n
3. If insufficient info, say "NOT_ENOUGH_INFO"

Data:
- Instructions: {instructions}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""

        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Generate structured PowerPoint content"},
                {"role": "user", "content": ppt_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        try:
            r = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            resp = r.json()
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    slides_text = generate_slide_content()
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
    if "NOT_ENOUGH_INFO" in slides_text.upper():
        return "Error: Insufficient information to generate slides"
    if len(slides_text) < 20:
        return "Error: Generated content too short or invalid"

    try:
        prs = Presentation()
        BG_COLOR = PPTRGBColor(234, 215, 194)  # #EAD7C2
        TEXT_COLOR = PPTRGBColor(193, 114, 80) # #C17250
        FONT_NAME = "Cairo"

        # Build slides
        for slide_content in slides_text.split('\n\n'):
            lines = [l.strip() for l in slide_content.split('\n') if l.strip()]
            if not lines:
                continue

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

        # Upload to blob
        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
            "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }
        ppt_buffer = io.BytesIO()
        prs.save(ppt_buffer)
        ppt_buffer.seek(0)

        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        blob_client = blob_service.get_container_client(
            blob_config["container"]
        ).get_blob_client(
            f"presentation_{datetime.now().strftime('%Y%m%d%H%M%S')}.pptx"
        )
        blob_client.upload_blob(ppt_buffer, overwrite=True)

        download_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{blob_client.blob_name}?"
            f"{blob_config['sas_token']}"
        )

        # Schedule auto-delete after 300 seconds
        threading.Timer(300, blob_client.delete_blob).start()
        return download_url
    except Exception as e:
        return f"Presentation Generation Error: {str(e)}"


##################################################
# Generate Charts
##################################################
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
        chart_prompt = f"""You are a converter that outputs ONLY valid JSON or "Information is not suitable for a chart".

JSON format:
{{
  "chart_type": "bar"|"line"|"column",
  "title": "string",
  "categories": ["Cat1","Cat2",...],
  "series": [
    {{"name":"Series1","values":[num1,num2,...]}},
    ...
  ]
}}

Data:
- Instructions: {instructions}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""

        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Output ONLY valid JSON or 'Information is not suitable for a chart'"},
                {"role": "user", "content": chart_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        try:
            r = requests.post(endpoint, headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            resp = r.json()
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    def create_chart_image(chart_data):
        try:
            plt.rcParams['axes.titleweight'] = 'bold'
            plt.rcParams['axes.titlesize'] = 12
            fig, ax = plt.subplots(figsize=(8,4.5))
            color_cycle = CHART_COLORS

            if chart_data['chart_type'] in ['bar','column']:
                # bar or column
                for idx, series in enumerate(chart_data['series']):
                    color = color_cycle[idx % len(color_cycle)]
                    ax.bar(
                        chart_data['categories'],
                        series['values'],
                        label=series['name'],
                        color=color,
                        width=0.6
                    )
            elif chart_data['chart_type'] == 'line':
                for idx, series in enumerate(chart_data['series']):
                    color = color_cycle[idx % len(color_cycle)]
                    ax.plot(
                        chart_data['categories'],
                        series['values'],
                        label=series['name'],
                        color=color,
                        marker='o',
                        linewidth=2.5
                    )
            else:
                return None

            ax.set_title(chart_data['title'])
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            plt.xticks(rotation=45, ha='right')
            plt.legend()
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150)
            buf.seek(0)
            plt.close()
            return buf
        except Exception as e:
            return None

    chart_response = generate_chart_data()
    if chart_response.startswith("API_ERROR:"):
        return f"OpenAI Error: {chart_response[10:]}"
    if chart_response.strip() == "Information is not suitable for a chart":
        return "Information is not suitable for a chart"

    match = re.search(r'(\{.*\})', chart_response, re.DOTALL)
    if not match:
        return "Invalid chart data format: No JSON object found"
    json_str = match.group(1)
    try:
        chart_data = json.loads(json_str)
        for k in ['chart_type','title','categories','series']:
            if k not in chart_data:
                return f"Invalid chart data: missing '{k}'"
    except Exception as e:
        return f"Invalid chart data: {str(e)}"

    # Build a doc with the chart
    doc = Document()
    buf_img = create_chart_image(chart_data)
    if not buf_img:
        return "Failed to generate chart from data"

    doc.add_heading(chart_data['title'], level=1)
    doc.add_picture(buf_img, width=Inches(6))
    p = doc.add_paragraph("Source: Generated from provided data")
    p.alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT

    blob_config = {
        "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
        "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
        "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
    }
    doc_buf = io.BytesIO()
    doc.save(doc_buf)
    doc_buf.seek(0)

    blob_service = BlobServiceClient(
        account_url=blob_config["account_url"],
        credential=blob_config["sas_token"]
    )
    blob_client = blob_service.get_container_client(
        blob_config["container"]
    ).get_blob_client(
        f"chart_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
    )
    blob_client.upload_blob(doc_buf, overwrite=True)

    download_url = (
        f"{blob_config['account_url']}/"
        f"{blob_config['container']}/"
        f"{blob_client.blob_name}?"
        f"{blob_config['sas_token']}"
    )
    threading.Timer(300, blob_client.delete_blob).start()
    return download_url


##################################################
# Generate Documents
##################################################
def Call_DOC(latest_question, latest_answer, chat_history, instructions_doc):
    from docx import Document
    from docx.shared import Pt as DocxPt, RGBColor as DocxRGBColor
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml

    def generate_doc_content():
        chat_history_str = str(chat_history)
        doc_prompt = f"""You are a professional document writer. 
Use ONLY the provided info to create a structured document:
Format:
Section Heading\\n- Bullet 1\\n- Bullet 2
Separate sections with \\n\\n
If insufficient info, say "Not enough Information to perform export."

Data:
- Instructions: {instructions_doc}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""

        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Generate structured document content"},
                {"role": "user", "content": doc_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        try:
            r = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            resp = r.json()
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"API_ERROR:
