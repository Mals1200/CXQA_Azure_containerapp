import re
import requests
import json
import io
import threading
from datetime import datetime

# Azure Blob Storage
from azure.storage.blob import BlobServiceClient

# Azure OpenAI API Config
OPENAI_ENDPOINT = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
OPENAI_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

# Azure Blob Storage Config
BLOB_CONFIG = {
    "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
    "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
}

##################################################
# Helper Function: Upload File to Azure Blob
##################################################
def upload_to_blob(buffer, filename, filetype):
    """
    Uploads a file to Azure Blob Storage and returns the download link.
    """
    blob_service = BlobServiceClient(
        account_url=BLOB_CONFIG["account_url"],
        credential=BLOB_CONFIG["sas_token"]
    )
    blob_client = blob_service.get_container_client(
        BLOB_CONFIG["container"]
    ).get_blob_client(filename)

    blob_client.upload_blob(buffer, overwrite=True)
    download_url = (
        f"{BLOB_CONFIG['account_url']}/"
        f"{BLOB_CONFIG['container']}/"
        f"{filename}?{BLOB_CONFIG['sas_token']}"
    )

    # Schedule deletion after 5 minutes
    threading.Timer(300, blob_client.delete_blob).start()

    return f"The link to your {filetype}:\n{download_url}"


##################################################
# Generate PowerPoint function
##################################################
def Call_PPT(latest_question, latest_answer, chat_history, instructions):
    from pptx import Presentation
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    def generate_slide_content():
        chat_history_str = str(chat_history)

        ppt_prompt = f"""You are a PowerPoint expert. Use this information to create slides:
Rules:
1. Use ONLY the provided information
2. Output ready-to-use slide text
3. Format: Slide Title\\n- Bullet 1\\n- Bullet 2
4. Separate slides with \\n\\n
5. If insufficient information, say exactly: "INSUFFICIENT_INFO"

Data:
- Instructions: {instructions}
- Question: {latest_question}
- Answer: {latest_answer}
- History: {chat_history_str}"""

        headers = {"Content-Type": "application/json", "api-key": OPENAI_API_KEY}

        payload = {
            "messages": [{"role": "system", "content": "Generate PowerPoint content"},
                         {"role": "user", "content": ppt_prompt}],
            "max_tokens": 1000,
            "temperature": 0.3
        }

        try:
            response = requests.post(OPENAI_ENDPOINT, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            result = response.json()["choices"][0]["message"]["content"].strip()
            return result
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    slides_text = generate_slide_content()

    # Handle error cases
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
    if "INSUFFICIENT_INFO" in slides_text:
        return "Error: Not enough information to generate slides."

    prs = Presentation()
    BG_COLOR = RGBColor(234, 215, 194)
    TEXT_COLOR = RGBColor(193, 114, 80)
    FONT_NAME = "Cairo"

    for slide_content in slides_text.split('\n\n'):
        lines = [line.strip() for line in slide_content.split('\n') if line.strip()]
        if not lines:
            continue

        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = BG_COLOR

        title_box = slide.shapes.add_textbox(Pt(50), Pt(50), prs.slide_width - Pt(100), Pt(60))
        title_frame = title_box.text_frame
        title_frame.text = lines[0]
        for paragraph in title_frame.paragraphs:
            paragraph.font.color.rgb = TEXT_COLOR
            paragraph.font.name = FONT_NAME
            paragraph.font.size = Pt(36)
            paragraph.alignment = PP_ALIGN.CENTER

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

    return upload_to_blob(ppt_buffer, f"presentation_{datetime.now().strftime('%Y%m%d%H%M%S')}.pptx", "Slides")


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
