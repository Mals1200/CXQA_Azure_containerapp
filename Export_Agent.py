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
        prompt = f"""You are a PPT expert. Format slides as text:
Slide Title\\n- Bullet 1\\n- Bullet 2
Separate slides with \\n\\n
If not enough info, say "NOT_ENOUGH_INFO"

Data:
Instructions: {instructions}
Question: {latest_question}
Answer: {latest_answer}
History: {chat_history_str}
"""
        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Generate structured PowerPoint content."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        try:
            r = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            js = r.json()
            return js["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    slides_text = generate_slide_content()
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
    if "NOT_ENOUGH_INFO" in slides_text.upper():
        return "Error: Not enough information to generate slides"
    if len(slides_text) < 20:
        return "Error: Generated slide content too short or invalid"

    try:
        prs = Presentation()
        BG_COLOR = PPTRGBColor(234, 215, 194)  # #EAD7C2
        TEXT_COLOR = PPTRGBColor(193, 114, 80) # #C17250
        FONT_NAME = "Cairo"

        for block in slides_text.split("\n\n"):
            lines = [x.strip() for x in block.split("\n") if x.strip()]
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
                    p.text = bullet.replace("- ", "").strip()
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
        url = f"{blob_config['account_url']}/{blob_config['container']}/{blob_client.blob_name}?{blob_config['sas_token']}"

        # Auto-delete after 300s
        threading.Timer(300, blob_client.delete_blob).start()

        return url
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
        prompt = f"""
You are a converter. Output ONLY valid JSON or the exact string "Information is not suitable for a chart".
JSON format:
{{
  "chart_type": "bar"|"line"|"column",
  "title": "string",
  "categories": ["Cat1","Cat2", ...],
  "series": [
    {{"name":"Series1", "values":[num1,num2,...]}}
  ]
}}
Data:
Instructions: {instructions}
Q: {latest_question}
A: {latest_answer}
History: {chat_history_str}
"""
        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Return only valid JSON or 'Information is not suitable for a chart'."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        try:
            r = requests.post(endpoint, headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    def create_chart_image(chart_data):
        try:
            plt.rcParams["axes.titleweight"] = "bold"
            plt.rcParams["axes.titlesize"] = 12
            fig, ax = plt.subplots(figsize=(8,4.5))

            if chart_data["chart_type"] in ["bar", "column"]:
                for idx, series in enumerate(chart_data["series"]):
                    color = CHART_COLORS[idx % len(CHART_COLORS)]
                    ax.bar(
                        chart_data["categories"],
                        series["values"],
                        label=series["name"],
                        color=color,
                        width=0.6
                    )
            elif chart_data["chart_type"] == "line":
                for idx, series in enumerate(chart_data["series"]):
                    color = CHART_COLORS[idx % len(CHART_COLORS)]
                    ax.plot(
                        chart_data["categories"],
                        series["values"],
                        label=series["name"],
                        color=color,
                        marker="o",
                        linewidth=2.5
                    )
            else:
                return None

            ax.set_title(chart_data["title"])
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            plt.xticks(rotation=45, ha="right")
            plt.legend()
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=150)
            buf.seek(0)
            plt.close()
            return buf
        except:
            return None

    chart_str = generate_chart_data()
    if chart_str.startswith("API_ERROR:"):
        return f"OpenAI Error: {chart_str[10:]}"
    if chart_str == "Information is not suitable for a chart":
        return "Information is not suitable for a chart"

    import re
    match = re.search(r"(\{.*\})", chart_str, re.DOTALL)
    if not match:
        return "Invalid chart data format: No JSON found"

    json_str = match.group(1)
    try:
        chart_data = json.loads(json_str)
        for key in ["chart_type", "title", "categories", "series"]:
            if key not in chart_data:
                return f"Chart data is missing '{key}'"
    except Exception as e:
        return f"Invalid chart data: {str(e)}"

    # Build a doc with the chart
    doc = Document()
    buf_img = create_chart_image(chart_data)
    if not buf_img:
        return "Failed to generate chart from data"

    doc.add_heading(chart_data["title"], level=1)
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
    url = f"{blob_config['account_url']}/{blob_config['container']}/{blob_client.blob_name}?{blob_config['sas_token']}"
    threading.Timer(300, blob_client.delete_blob).start()

    return url

##################################################
# Generate Word Document
##################################################
def Call_DOC(latest_question, latest_answer, chat_history, instructions_doc):
    from docx import Document
    from docx.shared import Pt as DocxPt, RGBColor as DocxRGBColor
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml

    def generate_doc_content():
        chat_history_str = str(chat_history)
        prompt = f"""
You are a professional doc writer. 
Format: 
Section Heading\\n- Bullet 1\\n- Bullet 2
Separate sections with \\n\\n
If insufficient info, say "Not enough Information"

Data:
Instructions: {instructions_doc}
Q: {latest_question}
A: {latest_answer}
History: {chat_history_str}
"""
        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Generate structured document content."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        try:
            rr = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            rr.raise_for_status()
            js = rr.json()
            return js["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    doc_text = generate_doc_content()
    if doc_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {doc_text[10:]}"
    if "NOT ENOUGH INFORMATION" in doc_text.upper():
        return "Error: Not enough information to generate doc"
    if len(doc_text) < 20:
        return "Error: Generated content too short or invalid"

    try:
        doc = Document()
        BG_COLOR_HEX = "EAD7C2"
        TITLE_COLOR = DocxRGBColor(193, 114, 80)
        BODY_COLOR = DocxRGBColor(0,0,0)
        FONT_NAME = "Cairo"
        TITLE_SIZE = DocxPt(16)
        BODY_SIZE = DocxPt(12)

        style = doc.styles["Normal"]
        style.font.name = FONT_NAME
        style.font.size = BODY_SIZE
        style.font.color.rgb = BODY_COLOR

        for section in doc.sections:
            sectPr = section._sectPr
            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{BG_COLOR_HEX}"/>')
            sectPr.append(shd)

        for block in doc_text.split("\n\n"):
            lines = [x.strip() for x in block.split("\n") if x.strip()]
            if not lines:
                continue

            heading = doc.add_heading(level=1)
            heading_run = heading.add_run(lines[0])
            heading_run.font.color.rgb = TITLE_COLOR
            heading_run.font.size = TITLE_SIZE
            heading_run.bold = True
            heading.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER

            if len(lines) > 1:
                for bullet in lines[1:]:
                    para = doc.add_paragraph(style="ListBullet")
                    run = para.add_run(bullet.replace("- ","").strip())
                    run.font.color.rgb = BODY_COLOR

            doc.add_paragraph()

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
            f"document_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
        )
        blob_client.upload_blob(doc_buf, overwrite=True)
        url = f"{blob_config['account_url']}/{blob_config['container']}/{blob_client.blob_name}?{blob_config['sas_token']}"

        threading.Timer(300, blob_client.delete_blob).start()
        return url
    except Exception as e:
        return f"Document Generation Error: {str(e)}"

##################################################
# The main function that picks PPT, Chart, or Doc
##################################################
def Call_Export(latest_question, latest_answer, chat_history, instructions):
    def generate_ppt():
        yield "⏳ Generating PowerPoint presentation...\n"
        url = Call_PPT(latest_question, latest_answer, chat_history, instructions)
        yield f"✅ PowerPoint created: {url}\n"

    def generate_chart():
        yield "⏳ Generating chart...\n"
        url = Call_CHART(latest_question, latest_answer, chat_history, instructions)
        yield f"✅ Chart created: {url}\n"

    def generate_doc():
        yield "⏳ Generating Word document...\n"
        url = Call_DOC(latest_question, latest_answer, chat_history, instructions)
        yield f"✅ Document created: {url}\n"

    instructions_lower = instructions.lower()

    # Simple detection
    if re.search(r"\b(presentation|slide|powerpoint|ppt|deck)\b", instructions_lower):
        yield from generate_ppt()
    elif re.search(r"\b(chart|graph|diagram|plot)\b", instructions_lower):
        yield from generate_chart()
    elif re.search(r"\b(document|report|word doc|proposal|paper|memo|contract)\b", instructions_lower):
        yield from generate_doc()
    else:
        yield "Not enough Information to perform export."
