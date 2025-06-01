# Version 4b
# call SOP has more efficient prompt & has a better layout:
    # The logo and art images are centered
    # Can manipulate the art image using ratios and scalinf
    # The prompt is more effiecient and uses less tokens.


import re
import requests
import json
import io
import threading
import time
from datetime import datetime

#SOP imports######
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

##################################################
# Azure Blob Storage
from azure.storage.blob import BlobServiceClient


##################################################
# HELPER: Retry-Enabled OpenAI Call
##################################################
def openai_call_with_retry(endpoint, headers, payload, max_attempts=3, backoff=5, timeout=30):
    """
    Makes an OpenAI POST request, retrying up to `max_attempts` times if an error occurs.
    :param endpoint: Full URL endpoint of the Azure OpenAI service
    :param headers: Dict of HTTP headers (including 'api-key')
    :param payload: JSON body for the request
    :param max_attempts: Number of times to retry before giving up
    :param backoff: Seconds to wait between retries
    :param timeout: HTTP request timeout in seconds
    :return: The JSON-decoded response or a dict with "error" if all attempts fail
    """
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


##################################################
# HELPER: Upload File to Azure Blob
##################################################
def upload_to_azure_blob(blob_config, file_buffer, file_name_prefix):
    """
    Uploads a file buffer to Azure Blob Storage with a given prefix in the file name.
    Automatically schedules deletion after 5 minutes (300 seconds).
    :param blob_config: dict with account_url, sas_token, and container
    :param file_buffer: io.BytesIO or similar buffer
    :param file_name_prefix: e.g. "presentation", "chart", "document"
    :return: download_url string
    """
    try:
        # Build the blob client
        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        container_client = blob_service.get_container_client(blob_config["container"])
        file_name = f"{file_name_prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # We'll guess the extension outside if needed, so add it when calling this helper if you like.
        blob_client = container_client.get_blob_client(file_name)
        
        # Upload and generate URL
        blob_client.upload_blob(file_buffer, overwrite=True)
        download_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{file_name}?"
            f"{blob_config['sas_token']}"
        )
        
        # Schedule auto-delete after 300 seconds
        threading.Timer(300, blob_client.delete_blob).start()
        return download_url

    except Exception as e:
        raise Exception(f"Azure Blob Upload Error: {str(e)}")


##################################################
# Generate PowerPoint function
##################################################
def Call_PPT(latest_question, latest_answer, chat_history, instructions):
    # PowerPoint imports
    from pptx import Presentation
    from pptx.util import Pt
    from pptx.dml.color import RGBColor as PPTRGBColor
    from pptx.enum.text import PP_ALIGN
    
    ##################################################
    # (A) IMPROVED AZURE OPENAI CALL
    ##################################################
    def generate_slide_content():
        chat_history_str = str(chat_history)
        
        ppt_prompt = f"""You are a PowerPoint presentation expert. Use this information to create slides:
Rules:
1. Use ONLY the provided information
2. Output ready-to-use slide text
3. Format: Slide Title\\n- Bullet 1\\n- Bullet 2
4. Separate slides with \\n\\n
5. If insufficient information, say: \"NOT_ENOUGH_INFO\"

Data:
- Instructions: {instructions}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""

        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview"
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

        # Use our retry-enabled helper
        result_json = openai_call_with_retry(endpoint, headers, payload, max_attempts=3, backoff=5, timeout=30)
        if "error" in result_json:
            return result_json["error"]  # e.g. "API_ERROR: <details>"
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    ##################################################
    # (B) ROBUST CONTENT HANDLING
    ##################################################
    slides_text = generate_slide_content()
    
    # Handle error cases
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
    if "NOT_ENOUGH_INFO" in slides_text:
        return "Error: Insufficient information to generate slides"
    if len(slides_text) < 20:
        return "Error: Generated content too short or invalid"

    ##################################################
    # (C) SLIDE GENERATION WITH DESIGN
    ##################################################
    try:
        prs = Presentation()

        BG_COLOR   = PPTRGBColor(234, 215, 194)  # #EAD7C2
        TEXT_COLOR = PPTRGBColor(193, 114, 80)   # #C17250
        FONT_NAME  = "Cairo"
        
        def choose_font_size(text, max_chars, large_size, small_size):
            """
            If text length > max_chars, return small_size. Otherwise large_size.
            """
            return Pt(small_size if len(text) > max_chars else large_size)

        # Split each "slide" block at double newlines
        for slide_block in slides_text.split('\n\n'):
            lines = [line.strip() for line in slide_block.split('\n') if line.strip()]
            if not lines:
                # skip empty slide entirely
                continue
            
            # If there are more than 6 bullets, break them into two slides:
            title_text   = lines[0]
            bullet_lines = lines[1:]
            chunks = []
            if len(bullet_lines) > 6:
                # Split into chunks of <= 6 lines each
                for i in range(0, len(bullet_lines), 6):
                    chunks.append(bullet_lines[i:i+6])
            else:
                chunks.append(bullet_lines)
            
            # For each chunk, create a separate slide with the same title
            for chunk in chunks:
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                slide.background.fill.solid()
                slide.background.fill.fore_color.rgb = BG_COLOR
                
                # (1) Title box
                title_box = slide.shapes.add_textbox(
                    Pt(50), Pt(50), prs.slide_width - Pt(100), Pt(60)
                )
                title_frame = title_box.text_frame
                title_frame.text = title_text
                for paragraph in title_frame.paragraphs:
                    paragraph.font.color.rgb = TEXT_COLOR
                    paragraph.font.name = FONT_NAME
                    paragraph.font.size = choose_font_size(title_text, max_chars=30, large_size=36, small_size=28)
                    paragraph.alignment = PP_ALIGN.CENTER
                    
                # (2) Bullets box
                if chunk:
                    content_box = slide.shapes.add_textbox(
                        Pt(100), Pt(150), prs.slide_width - Pt(200), prs.slide_height - Pt(250)
                    )
                    content_frame = content_box.text_frame
                    # Clear any pre-existing bullet
                    content_frame.clear()
                    for bullet in chunk:
                        p = content_frame.add_paragraph()
                        txt = bullet.replace('- ', '').strip()
                        p.text = txt
                        p.font.color.rgb = TEXT_COLOR
                        p.font.name = FONT_NAME
                        p.font.size = choose_font_size(txt, max_chars=50, large_size=24, small_size=18)
                        p.space_after = Pt(8)

        ##################################################
        # (D) FILE UPLOAD
        ##################################################
        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
            "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }

        ppt_buffer = io.BytesIO()
        prs.save(ppt_buffer)
        ppt_buffer.seek(0)

        file_name_prefix = f"presentation_{datetime.now().strftime('%Y%m%d%H%M%S')}.pptx"
        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        blob_client = blob_service.get_container_client(
            blob_config["container"]
        ).get_blob_client(file_name_prefix)
        
        blob_client.upload_blob(ppt_buffer, overwrite=True)
        download_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{blob_client.blob_name}?{blob_config['sas_token']}"
        )
        
        threading.Timer(300, blob_client.delete_blob).start()

        return f"Here is your generated slides:\n{download_url}"

    except Exception as e:
        return f"Presentation Generation Error: {str(e)}"


##################################################
# Generate Charts function
##################################################
def Call_CHART(latest_question, latest_answer, chat_history, instructions):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    from docx import Document
    from docx.shared import Inches
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    import json
    import io

    # Chart color palette for aesthetic purposes
    CHART_COLORS = [
        (193/255, 114/255, 80/255),   # Reddish
        (85/255, 20/255, 45/255),     # Dark Wine
        (219/255, 188/255, 154/255),  # Lighter Brown
        (39/255, 71/255, 54/255),     # Dark Green
        (254/255, 200/255, 65/255)    # Yellow
    ]

    ##################################################
    # (A) Improved Azure OpenAI Call for Chart Data
    ##################################################
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

        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }

        payload = {
            "messages": [
                {"role": "system", "content": "Output ONLY valid JSON as described."},
                {"role": "user", "content": chart_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }

        result_json = openai_call_with_retry(endpoint, headers, payload, max_attempts=3, backoff=5, timeout=30)
        if "error" in result_json:
            return result_json["error"]
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    ##################################################
    # (B) Chart Generation Logic - Robust Handling
    ##################################################
    def create_chart_image(chart_data):
        try:
            plt.rcParams['axes.titleweight'] = 'bold'
            plt.rcParams['axes.titlesize'] = 12

            # Check if the necessary keys exist
            if not all(key in chart_data for key in ['chart_type', 'title', 'categories', 'series']):
                raise ValueError("Missing required keys in chart data. Ensure chart_type, title, categories, and series are present.")
            
            fig, ax = plt.subplots(figsize=(8, 4.5))
            color_cycle = CHART_COLORS

            # Determine chart type and plot accordingly
            if chart_data['chart_type'] in ['bar', 'column']:
                handle = ax.bar
            elif chart_data['chart_type'] == 'line':
                handle = ax.plot
            else:
                raise ValueError(f"Unsupported chart type: {chart_data['chart_type']}")

            # Plot each series
            for idx, series in enumerate(chart_data['series']):
                color = color_cycle[idx % len(color_cycle)]
                if chart_data['chart_type'] in ['bar', 'column']:
                    handle(
                        chart_data['categories'],
                        series['values'],
                        label=series['name'],
                        color=color,
                        width=0.6
                    )
                else:  # line chart
                    handle(
                        chart_data['categories'],
                        series['values'],
                        label=series['name'],
                        color=color,
                        marker='o',
                        linewidth=2.5
                    )

            # Finalize chart appearance
            ax.set_title(chart_data['title'])
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            plt.xticks(rotation=45, ha='right')
            has_labels = any(series.get("name", "").strip() for series in chart_data["series"])
            if has_labels:
                plt.legend()
            plt.tight_layout()

            # Save chart to image buffer
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', dpi=150)
            img_buffer.seek(0)
            plt.close()
            return img_buffer

        except Exception as e:
            print(f"Chart Error: {str(e)}")
            return None

    ##################################################
    # (C) Main Processing Flow for Chart Generation
    ##################################################
    try:
        chart_response = generate_chart_data()

        if chart_response.startswith("API_ERROR:"):
            return f"OpenAI Error: {chart_response[10:]}"

        if chart_response.strip() == "Information is not suitable for a chart":
            return "Information is not suitable for a chart"

        match = re.search(r'(\{.*\})', chart_response, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            return "Invalid chart data format: No JSON object found"

        try:
            chart_data = json.loads(json_str)
            if not all(k in chart_data for k in ['chart_type', 'title', 'categories', 'series']):
                raise ValueError("Missing required keys in chart data: 'chart_type', 'title', 'categories', or 'series'.")
        except Exception as e:
            return f"Invalid chart data format: {str(e)}"

        # Create chart image
        img_buffer = create_chart_image(chart_data)
        if not img_buffer:
            return "Failed to generate chart from data"

        # Create Word document to include the chart
        doc = Document()
        doc.add_heading(chart_data['title'], level=1)
        doc.add_picture(img_buffer, width=Inches(6))
        para = doc.add_paragraph("Source: Generated from provided data")
        para.alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT

        # Upload to Azure Blob Storage
        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
            "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }

        doc_buffer = io.BytesIO()
        doc.save(doc_buffer)
        doc_buffer.seek(0)

        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        blob_client = blob_service.get_container_client(
            blob_config["container"]
        ).get_blob_client(
            f"chart_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
        )
        blob_client.upload_blob(doc_buffer, overwrite=True)
        download_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{blob_client.blob_name}?{blob_config['sas_token']}"
        )

        # Automatically delete the blob after 5 minutes
        threading.Timer(300, blob_client.delete_blob).start()

        return f"Here is your generated chart:\n{download_url}"

    except Exception as e:
        return f"Chart Generation Error: {str(e)}"


##################################################
# Generate Documents function
##################################################
def Call_DOC(latest_question, latest_answer, chat_history, instructions_doc):
    """
    Simple Document generator: 
      - First line of instructions_doc is used as the Title.
      - The rest of instructions_doc is placed verbatim below under a 'Body' heading.
      - Saves as .docx, uploads to Azure Blob, returns URL.
    """
    from docx import Document
    from docx.shared import Pt as DocxPt, RGBColor as DocxRGBColor
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml
    import io
    import threading
    from datetime import datetime
    from azure.storage.blob import BlobServiceClient

    # 1) If the user didn't provide any instructions, bail out:
    if not instructions_doc or not instructions_doc.strip():
        return "Error: No instructions provided to generate document."
    
    # 2) Split instructions_doc into lines. First non-empty line is Title:
    lines = [line for line in instructions_doc.splitlines() if line.strip()]
    if len(lines) == 0:
        return "Error: Instructions were empty or whitespace."
    
    doc_title = lines[0].strip()
    body_lines = lines[1:]  # everything after line 0

    try:
        # 3) Create a new DOCX
        doc = Document()

        # 3a) Set default "Cairo" font, 12pt, as you liked:
        style = doc.styles['Normal']
        style.font.name = "Cairo"
        style.font.size = DocxPt(12)
        style.font.color.rgb = DocxRGBColor(0, 0, 0)

        # 3b) Light-gray page background if desired (optional; comment out if not)
        for section in doc.sections:
            sectPr = section._sectPr
            shd = parse_xml(r'<w:shd {} w:fill="EAD7C2"/>'.format(nsdecls('w')))
            sectPr.append(shd)

        # 3c) Add the Title as a Heading 1
        para_title = doc.add_heading(level=1)
        run_t = para_title.add_run(doc_title)
        run_t.font.name = "Cairo"
        run_t.font.size = DocxPt(16)
        run_t.bold = True

        # 4) If there are any "body_lines," put them under a "Body" heading
        if body_lines:
            doc.add_paragraph()  # blank line
            para_hdr = doc.add_heading(level=2)
            run_hdr = para_hdr.add_run("Body")
            run_hdr.font.name = "Cairo"
            run_hdr.font.size = DocxPt(14)
            run_hdr.bold = True

            for bl in body_lines:
                normal_para = doc.add_paragraph()
                run_n = normal_para.add_run(bl)
                run_n.font.name = "Cairo"
                run_n.font.size = DocxPt(12)

        # 5) Upload to Azure Blob
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)

        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": (
                "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
                "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&"
                "sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
            ),
            "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }
        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        # Name it "document_<timestamp>.docx"
        blob_client = blob_service.get_container_client(
            blob_config["container"]
        ).get_blob_client(
            f"document_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
        )
        blob_client.upload_blob(buf, overwrite=True)

        download_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{blob_client.blob_name}?{blob_config['sas_token']}"
        )

        # Delete after 5 minutes
        threading.Timer(300, blob_client.delete_blob).start()

        return f"Here is your generated Document:\n{download_url}"

    except Exception as e:
        return f"Document Generation Error: {str(e)}"


def Call_SOP(latest_question, latest_answer, chat_history, instructions):
    """
    Generates a Standard Operating Procedure (SOP) PDF by:
      1) Asking GPT for a JSON object with these fields:
         title, table_of_contents, overview, scope, policy, provisions,
         definitions, process_responsibilities, process, procedures,
         related_docs, sop_form, sop_log
      2) Parsing that JSON and building a multi-page PDF with:
         - A front cover (logo + metadata)
         - One section per JSON key (with headings/styles)
      3) Uploading the final PDF to Azure Blob and returning the URL.
    """
    import re
    import json
    import io
    import threading
    from datetime import datetime

    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.utils import ImageReader
    from azure.storage.blob import BlobServiceClient
    import fitz  # PyMuPDF

    ##################################################
    # (A) GPT Call: Return ONLY valid JSON with SOP fields
    ##################################################
    def generate_sop_content():
        """
        Calls Azure OpenAI with a prompt that forces valid, escaped JSON.
        """
        chat_history_str = str(chat_history)

        sop_prompt = f"""
You are an SOP writer. Based on the provided information below, return exactly one valid JSON object—nothing else.

Each value inside that JSON must escape any internal double-quotes (e.g. use \\\" inside values).  
The JSON MUST begin with a single "{{" on its own line and end with a single "}}" on its own line (no leading/trailing text).

The required structure (all keys must appear, even if empty) is:
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

**IMPORTANT RULES**:
1. Do NOT include any triple backticks or code fences.
2. Do NOT include any text before the first "{{" or after the final "}}".
3. Every string value inside this JSON must escape internal quotes (e.g.: \\"like this\\").
4. If a section is missing, return an empty string for that key (not "null").
5. Use valid JSON syntax—commas between entries, double quotes around keys and string values.

Example of EXACTLY acceptable output:
{{
  "title": "Example Title",
  "table_of_contents": "Section 1, Section 2",
  "overview": "This is an overview.",
  "scope": "This is the scope.",
  "policy": "Policy text here.",
  "provisions": "Provision text here.",
  "definitions": "Definition text here.",
  "process_responsibilities": "Responsibilities text.",
  "process": "Process details.",
  "procedures": "Procedure details.",
  "related_docs": "Related Document A",
  "sop_form": "Form details.",
  "sop_log": "Log details."
}}

The information to use:
- Conversation history: {chat_history_str}
- User request: {latest_question}
- Final answer to the user: {latest_answer}
- User instructions: {instructions}
"""
        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview"
        headers = {
            "Content-Type": "application/json",
            "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
        }
        payload = {
            "messages": [
                {"role": "system", "content": "Generate SOP content in a structured manner."},
                {"role": "user", "content": sop_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        # Use the existing retry helper
        result_json = openai_call_with_retry(endpoint, headers, payload, max_attempts=3, backoff=5, timeout=30)
        if "error" in result_json:
            return f"API_ERROR: {result_json['error']}"
        try:
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    ##################################################
    # (B) Receive raw JSON string & extract it cleanly
    ##################################################
    raw_json = generate_sop_content()

    # 1) Catch API errors or "Not enough info" responses
    if raw_json.startswith("API_ERROR:"):
        return f"OpenAI API Error: {raw_json[10:].strip()}"
    if "NOT_ENOUGH_INFO" in raw_json.upper():
        return "Error: Insufficient information to generate SOP"
    if len(raw_json.strip()) < 20:
        return "Error: Generated content too short or invalid"

    # 2) Extract exactly the JSON object between the first '{' and the final '}'  
    match = re.search(r'\{.*\}\Z', raw_json.strip(), flags=re.DOTALL)
    if not match:
        return "Error: Could not find a valid JSON object in LLM output."
    clean_json = match.group(0)

    # 3) Parse the cleaned JSON string
    try:
        sop_data = json.loads(clean_json)
    except json.JSONDecodeError as e:
        return f"Error: GPT output wasn't valid JSON. Details: {str(e)}"

    # 4) Ensure all keys exist (fill missing ones with empty string)
    for key in ["title", "table_of_contents", "overview", "scope",
                "policy", "provisions", "definitions",
                "process_responsibilities", "process", "procedures",
                "related_docs", "sop_form", "sop_log"]:
        if key not in sop_data:
            sop_data[key] = ""

    # 5) Build the five desired sections in order (some may be empty)
    sop_title        = sop_data.get("title", "")
    toc_text         = sop_data.get("table_of_contents", "")
    overview_text    = sop_data.get("overview", "")
    scope_text       = sop_data.get("scope", "")
    policy_text      = sop_data.get("policy", "")
    provisions_data  = sop_data.get("provisions", "")
    definitions_data = sop_data.get("definitions", "")
    proc_resp_data   = sop_data.get("process_responsibilities", "")
    process_text     = sop_data.get("process", "")
    procedures_data  = sop_data.get("procedures", "")
    related_docs     = sop_data.get("related_docs", "")
    sop_form         = sop_data.get("sop_form", "")
    sop_log          = sop_data.get("sop_log", "")

    ##################################################
    # (C) Generate the PDF (front cover + main content)
    ##################################################
    try:
        buffer_front = io.BytesIO()
        c = canvas.Canvas(buffer_front, pagesize=A4)
        page_width, page_height = A4

        # (i) Download logo & art if available
        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": ("sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
                          "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
                          "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"),
            "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }

        def fetch_image(img_name):
            blob_service = BlobServiceClient(
                account_url=blob_config["account_url"],
                credential=blob_config["sas_token"]
            )
            container_client = blob_service.get_container_client(blob_config["container"])
            blob_client = container_client.get_blob_client(img_name)
            img_data = io.BytesIO()
            blob_client.download_blob().readinto(img_data)
            img_data.seek(0)
            return img_data

        try:
            logo_img = ImageReader(fetch_image("UI/2024-11-20_142337_UTC/cxqa_data/export-resources/logo.png"))
            art_img  = ImageReader(fetch_image("UI/2024-11-20_142337_UTC/cxqa_data/export-resources/art.png"))
        except:
            logo_img = None
            art_img  = None

        # --- Front page: logo + art + title ---
        if logo_img:
            logo_width = 70
            c.drawImage(logo_img,
                        (page_width - logo_width) / 2,
                        page_height - 150,
                        width=logo_width,
                        preserveAspectRatio=True,
                        mask='auto')
        if art_img:
            o_w, o_h = art_img.getSize()
            ratio = 0.8
            scaled_w = o_w * ratio
            scaled_h = o_h * ratio
            x_pos = (page_width - scaled_w) / 2
            y_pos = 0
            c.drawImage(art_img, x=x_pos, y=y_pos,
                        width=scaled_w, height=scaled_h,
                        preserveAspectRatio=False, mask='auto')

        # Title on front
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(colors.black)
        c.drawCentredString(page_width/2, page_height - 230, sop_title or "Untitled SOP")

        # Subtitle
        c.setFont("Helvetica", 12)
        c.setFillColor(colors.HexColor("#777777"))
        c.drawCentredString(page_width/2, page_height - 250,
                            "Standard Operating Procedure Document")

        # Metadata
        c.setFont("Helvetica", 10)
        meta_y = page_height - 310
        meta_lines = [
            f"Document Name: {sop_title or 'Untitled SOP'}",
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

        # --- Main sections ---
        style_heading = ParagraphStyle(
            'heading',
            fontName='Helvetica-Bold',
            fontSize=12,
            textColor=colors.HexColor("#C17250"),
            spaceAfter=6
        )
        style_text = ParagraphStyle(
            'text',
            fontName='Helvetica',
            fontSize=10,
            leading=14,
            spaceAfter=10
        )

        def add_section(title, content, story):
            if not content:
                return
            story.append(Paragraph(title, style_heading))
            if isinstance(content, list):
                for item in content:
                    story.append(Paragraph(f"- {item}", style_text))
            elif isinstance(content, dict):
                for k,v in content.items():
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, indent=2)
                    story.append(Paragraph(f"{k}: {v}", style_text))
            else:
                for line in str(content).split("\n"):
                    if line.strip():
                        story.append(Paragraph(line.strip(), style_text))
            story.append(Spacer(1, 12))

        buffer_content = io.BytesIO()
        doc_story = []

        # Build each of the  five sections in order
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

        # Merge front page + content into one PDF
        c.save()
        buffer_content.seek(0)
        front_pdf = fitz.open(stream=buffer_front.getvalue(), filetype="pdf")
        content_pdf = fitz.open(stream=buffer_content.getvalue(), filetype="pdf")
        front_pdf.insert_pdf(content_pdf)

        final_output = io.BytesIO()
        front_pdf.save(final_output)
        final_output.seek(0)

        # Upload final PDF to Azure Blob
        blob_service = BlobServiceClient(
            account_url=blob_config["account_url"],
            credential=blob_config["sas_token"]
        )
        container_client = blob_service.get_container_client(blob_config["container"])
        blob_name = f"sop_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(final_output, overwrite=True)

        final_url = (
            f"{blob_config['account_url']}/"
            f"{blob_config['container']}/"
            f"{blob_name}?{blob_config['sas_token']}"
        )

        threading.Timer(300, blob_client.delete_blob).start()
        return f"Here is your generated SOP:\n{final_url}"

    except Exception as e:
        return f"SOP Generation Error: {str(e)}"


##################################################
# Calling the export function
##################################################
def Call_Export(latest_question, latest_answer, chat_history, instructions):
    import re

    def generate_ppt():
        return Call_PPT(latest_question, latest_answer, chat_history, instructions)

    def generate_doc():
        return Call_DOC(latest_question, latest_answer, chat_history, instructions)

    def generate_chart():
        return Call_CHART(latest_question, latest_answer, chat_history, instructions)

    instructions_lower = instructions.lower()

    # 1) PPT?
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
        return [generate_ppt()]

    # 2) Chart?
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
        return [generate_chart()]

    # 3) Document?  (more inclusive now)
    elif re.search(
        r"\b("
        r"document[s]?|word\s+document[s]?|word[-\s]?doc[s]?|docx?"
        r")\b",
        instructions_lower,
        re.IGNORECASE
    ):
        return [generate_doc()]

    # 4) SOP?
    elif re.search(
        r"\b(standard operating procedure document|standard operating procedure|sop\.?)\b",
        instructions_lower, re.IGNORECASE
    ):
        return [Call_SOP(latest_question, latest_answer, chat_history, instructions)]

    # 5) Fallback
    return ["Not enough Information to perform export."]
