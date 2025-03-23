import re
import requests
import json
import io
import threading
import time
from datetime import datetime

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
5. If insufficient information, say: "NOT_ENOUGH_INFO"

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

        BG_COLOR = PPTRGBColor(234, 215, 194)  # #EAD7C2
        TEXT_COLOR = PPTRGBColor(193, 114, 80) # #C17250
        FONT_NAME = "Cairo"
        
        for slide_content in slides_text.split('\n\n'):
            lines = [line.strip() for line in slide_content.split('\n') if line.strip()]
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
                    p.space_after = Pt(12)

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

        # Reuse our helper to upload
        file_name_prefix = f"presentation_{datetime.now().strftime('%Y%m%d%H%M%S')}.pptx"
        # We'll just do the entire final name in the prefix to keep old naming style:
        # Or we can simplify. Let's keep it exactly the same as before for compatibility.
        # So we won't use a . in the prefix. We'll do the same logic as prior lines:
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
            f"{blob_client.blob_name}?"
            f"{blob_config['sas_token']}"
        )
        
        # Auto-delete after 5 minutes
        threading.Timer(300, blob_client.delete_blob).start()

        # SINGLE-LINE RETURN
        export_type = "slides"
        return f"Here is your generated {export_type}:\n{download_url}"

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
    import requests
    import re
    import threading
    import time
    from datetime import datetime
    from azure.storage.blob import BlobServiceClient

    CHART_COLORS = [
        (193/255, 114/255, 80/255),
        (85/255, 20/255, 45/255),
        (219/255, 188/255, 154/255),
        (39/255, 71/255, 54/255),
        (254/255, 200/255, 65/255)
    ]

    def generate_chart_data():
        chat_history_str = str(chat_history)

        # Instead of forcing only JSON or "Information is not suitable for a chart",
        # let's instruct the LLM but also parse out partial JSON if we can.
        chart_prompt = f"""You are a converter that tries to produce valid JSON for a chart.
Output either:
1) A JSON object of the form:
{{
  "chart_type": "bar"|"line"|"column",
  "title": "string",
  "categories": ["cat1","cat2",...],
  "series": [
    {{ "name": "Series1", "values": [num1, num2, ...] }},
    ...
  ]
}}

2) Or at least give me rough data. If you have no data, produce minimal placeholders:
{{
  "chart_type": "bar",
  "title": "Minimal Chart",
  "categories": ["A","B","C"],
  "series": [ {{ "name": "Default", "values": [1,2,3] }} ]
}}

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
                {"role": "system", "content": "Output valid JSON or minimal placeholders if no data."},
                {"role": "user", "content": chart_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }

        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            result_json = resp.json()
            return result_json['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    def create_chart_image(chart_data):
        try:
            plt.rcParams['axes.titleweight'] = 'bold'
            plt.rcParams['axes.titlesize'] = 12

            if not all(key in chart_data for key in ['chart_type', 'title', 'categories', 'series']):
                raise ValueError("Missing required keys in chart data. Must have chart_type, title, categories, series.")

            fig, ax = plt.subplots(figsize=(8, 4.5))
            color_cycle = CHART_COLORS

            if chart_data['chart_type'] in ['bar', 'column']:
                handle = ax.bar
            elif chart_data['chart_type'] == 'line':
                handle = ax.plot
            else:
                raise ValueError(f"Unsupported chart type: {chart_data['chart_type']}")

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
                else:
                    handle(
                        chart_data['categories'],
                        series['values'],
                        label=series['name'],
                        color=color,
                        marker='o',
                        linewidth=2.5
                    )

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
            print(f"Chart Error: {str(e)}")
            return None

    # Main flow
    chart_response = generate_chart_data()
    if chart_response.startswith("API_ERROR:"):
        return f"OpenAI Error: {chart_response[10:]}"

    # Attempt to parse JSON from the LLM response
    # We'll do a simpler approach: find the first brace and parse from there
    brace_index = chart_response.find('{')
    if brace_index < 0:
        # No brace found => produce a minimal fallback chart
        chart_data = {
            "chart_type": "bar",
            "title": "Fallback Chart",
            "categories": ["A","B","C"],
            "series": [{"name": "Series", "values": [10,20,30]}]
        }
    else:
        raw_json_str = chart_response[brace_index:]
        try:
            chart_data = json.loads(raw_json_str)
        except:
            # If it fails to parse, also do a fallback
            chart_data = {
                "chart_type": "bar",
                "title": "Fallback Chart",
                "categories": ["X","Y","Z"],
                "series": [{"name": "Series", "values": [1,2,3]}]
            }

    img_buffer = create_chart_image(chart_data)
    if not img_buffer:
        return "Failed to generate chart from data or fallback."

    # Build a Word doc that has the chart
    doc = Document()
    doc.add_heading(chart_data['title'], level=1)
    doc.add_picture(img_buffer, width=Inches(6))
    para = doc.add_paragraph("Source: Generated from provided data")
    para.alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT

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

    threading.Timer(300, blob_client.delete_blob).start()
    return f"Here is your generated chart:\n{download_url}"




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

    # PPT?
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
        return generate_ppt()

    # Chart?
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
        return generate_chart()

    # Document?
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
        return generate_doc()

    # Fallback
    return "Not enough Information to perform export."
