import re
import requests
import json
import io
import threading
from datetime import datetime

# Azure Blob Storage
from azure.storage.blob import BlobServiceClient




    ##################################################
    # Generate PowerPoint function
    ##################################################
def Call_PPT(latest_question, latest_answer, chat_history, instructions):
    # PowerPoint imports
    from pptx import Presentation
    from pptx.util import Pt
    from pptx.dml.color import RGBColor as PPTRGBColor  # Alias for PowerPoint
    from pptx.enum.text import PP_ALIGN
  
    # (A) IMPROVED AZURE OPENAI CALL
    def generate_slide_content():
        chat_history_str = str(chat_history)
        
        # Fixed prompt with proper spelling
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

        # Azure OpenAI configuration
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
            response = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        
        except Exception as e:
            return f"API_ERROR: {str(e)}"


    # (B) ROBUST CONTENT HANDLING
    slides_text = generate_slide_content()
    
    # Handle error cases
    if slides_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {slides_text[10:]}"
        
    if "NOT_ENOUGH_INFO" in slides_text:
        return "Error: Insufficient information to generate slides"
        
    if len(slides_text) < 20:  # Minimum viable content length
        return "Error: Generated content too short or invalid"


    # (C) SLIDE GENERATION WITH DESIGN
    try:
        prs = Presentation()
        
        # Design configuration - USE PPTRGBColor instead of generic RGBColor
        BG_COLOR = PPTRGBColor(234, 215, 194)  # Beige background #ead7c2
        TEXT_COLOR = PPTRGBColor(193, 114, 80)  # Dark reddish text #c17250
        FONT_NAME = "Cairo"
        
        # Process slides
        for slide_content in slides_text.split('\n\n'):
            lines = [line.strip() for line in slide_content.split('\n') if line.strip()]
            if not lines:
                continue
                
            # Create slide with custom background
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = BG_COLOR
            
            # Add title
            title_box = slide.shapes.add_textbox(Pt(50), Pt(50), prs.slide_width-Pt(100), Pt(60))
            title_frame = title_box.text_frame
            title_frame.text = lines[0]
            
            # Style title
            for paragraph in title_frame.paragraphs:
                paragraph.font.color.rgb = TEXT_COLOR
                paragraph.font.name = FONT_NAME
                paragraph.font.size = Pt(36)
                paragraph.alignment = PP_ALIGN.CENTER
                
            # Add content if available
            if len(lines) > 1:
                content_box = slide.shapes.add_textbox(Pt(100), Pt(150), prs.slide_width-Pt(200), prs.slide_height-Pt(250))
                content_frame = content_box.text_frame
                
                for bullet in lines[1:]:
                    p = content_frame.add_paragraph()
                    p.text = bullet.replace('- ', '').strip()
                    p.font.color.rgb = TEXT_COLOR
                    p.font.name = FONT_NAME
                    p.font.size = Pt(24)
                    p.space_after = Pt(12)

   
        # (D) FILE UPLOAD WITH ERROR HANDLING
        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
            "container": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
        }

        ppt_buffer = io.BytesIO()
        prs.save(ppt_buffer)
        ppt_buffer.seek(0)

        blob_service = BlobServiceClient(account_url=blob_config["account_url"], credential=blob_config["sas_token"])
        blob_client = blob_service.get_container_client(blob_config["container"]).get_blob_client(
            f"presentation_{datetime.now().strftime('%Y%m%d%H%M%S')}.pptx"
        )
        
        blob_client.upload_blob(ppt_buffer, overwrite=True)
        download_url = f"{blob_config['account_url']}/{blob_config['container']}/{blob_client.blob_name}?{blob_config['sas_token']}"

        # Schedule cleanup
        threading.Timer(300, blob_client.delete_blob).start()

        return f"Here are your Slides:\n{download_url}"

    except Exception as e:
        return f"Presentation Generation Error: {str(e)}"





    ##################################################
    # Generate Charts function
    ##################################################
def Call_CHART(latest_question, latest_answer, chat_history, instructions):
    # Charting imports
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    # DOCX IMPORTS HERE
    from docx import Document
    from docx.shared import Inches
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT


    # (A) CHART COLOR PALETTE
    #     Use tuples directly for Matplotlib.
    CHART_COLORS = [
        (193/255, 114/255, 80/255),   # Reddish
        (85/255, 20/255, 45/255),     # Dark Wine
        (219/255, 188/255, 154/255),  # Lighter Brown
        (39/255, 71/255, 54/255),     # Dark Green
        (254/255, 200/255, 65/255)    # Yellow
    ]


    # (B) IMPROVED AZURE OPENAI CALL FOR CHART DATA
    def generate_chart_data():
        chat_history_str = str(chat_history)
        
        # Enforce strict JSON-only output.
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

        endpoint = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
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

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=20)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

   
    # (C) CHART GENERATION LOGIC
    def create_chart_image(chart_data):
        try:
            # Remove font family references for Cairo.
            plt.rcParams['axes.titleweight'] = 'bold'
            plt.rcParams['axes.titlesize'] = 12

            fig, ax = plt.subplots(figsize=(8, 4.5))
            color_cycle = CHART_COLORS

            # Identify chart type.
            if chart_data['chart_type'] in ['bar', 'column']:
                handle = ax.bar
            elif chart_data['chart_type'] == 'line':
                handle = ax.plot
            else:
                return None

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


    # (D) MAIN PROCESSING FLOW
    try:
        chart_response = generate_chart_data()

        # Debug: Print the raw response for troubleshooting (remove in production)
        # print("DEBUG raw chart_response:", repr(chart_response))

        # Check for API error.
        if chart_response.startswith("API_ERROR:"):
            return f"OpenAI Error: {chart_response[10:]}"

        # Check if the model returns the exact string for unsuitable data.
        if chart_response.strip() == "Information is not suitable for a chart":
            return "Information is not suitable for a chart"

        # Use regex to extract JSON portion from the response.
        match = re.search(r'(\{.*\})', chart_response, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            return "Invalid chart data format: No JSON object found"

        # Try to parse the JSON.
        try:
            chart_data = json.loads(json_str)
            if not all(key in chart_data for key in ['chart_type', 'title', 'categories', 'series']):
                raise ValueError("Missing keys in chart data")
        except Exception as e:
            return f"Invalid chart data format: {str(e)}"

        # Create a Word Document and add the chart image.
        doc = Document()
        img_buffer = create_chart_image(chart_data)
        if img_buffer:
            doc.add_heading(chart_data['title'], level=1)
            doc.add_picture(img_buffer, width=Inches(6))
            para = doc.add_paragraph("Source: Generated from provided data")
            para.alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT
        else:
            return "Failed to generate chart from data"

      
        # (E) AZURE STORAGE UPLOAD
        blob_config = {
            "account_url": "https://cxqaazureaihub8779474245.blob.core.windows.net",
            "sas_token": (
                "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
                "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
                "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
            ),
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
            f"{blob_client.blob_name}?"
            f"{blob_config['sas_token']}"
        )

        # Auto-delete the blob after 5 minutes.
        threading.Timer(300, blob_client.delete_blob).start()

        return f"Here is your Chart:\n{download_url}"

    except Exception as e:
        return f"Chart Generation Error: {str(e)}"




    ##################################################
    # Generate Documents function
    ##################################################
def Call_DOC(latest_question, latest_answer, chat_history, instructions_doc):
    # Word Document imports
    from docx import Document
    from docx.shared import Pt as DocxPt, Inches, RGBColor as DocxRGBColor  # Alias for Word
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml

    # (A) AZURE OPENAI CONTENT GENERATION
    def generate_doc_content():
        chat_history_str = str(chat_history)
        
        doc_prompt = f"""You are a professional document writer. Use this information to create content:
Rules:
1. Use ONLY the provided information
2. Output ready-to-use document text
3. Format: 
   Section Heading\\n- Bullet 1\\n- Bullet 2
4. Separate sections with \\n\\n
5. If insufficient information, say: "NOT_ENOUGH_INFO"

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
            response = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"API_ERROR: {str(e)}"

    # (B) CONTENT VALIDATION
    doc_text = generate_doc_content()
    
    if doc_text.startswith("API_ERROR:"):
        return f"OpenAI API Error: {doc_text[10:]}"
    if "NOT_ENOUGH_INFO" in doc_text:
        return "Error: Insufficient information to generate document"
    if len(doc_text) < 20:
        return "Error: Generated content too short or invalid"

 
    # (C) DOCUMENT GENERATION
    try:
        doc = Document()
        
        # ===== DESIGN CONFIGURATION =====
        BG_COLOR = RGBColor(234, 215, 194)  # #ead7c2 as RGB tuple
        TITLE_COLOR = RGBColor(193, 114, 80)  # #c17250
        BODY_COLOR = RGBColor(0, 0, 0)       # Black
        FONT_NAME = "Cairo"
        TITLE_SIZE = Pt(16)
        BODY_SIZE = Pt(12)

        # Set base document styles
        style = doc.styles['Normal']
        style.font.name = FONT_NAME
        style.font.size = BODY_SIZE
        style.font.color.rgb = BODY_COLOR

        # Add background color to all sections (FIXED HERE)
        for section in doc.sections:
            sectPr = section._sectPr
            hex_color = f"{BG_COLOR[0]:02x}{BG_COLOR[1]:02x}{BG_COLOR[2]:02x}"
            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
            sectPr.append(shd)

        # Process content sections
        for section_content in doc_text.split('\n\n'):
            lines = [line.strip() for line in section_content.split('\n') if line.strip()]
            if not lines:
                continue

            # Add title
            heading = doc.add_heading(level=1)
            heading.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            heading_run = heading.add_run(lines[0])
            heading_run.font.color.rgb = TITLE_COLOR
            heading_run.font.size = TITLE_SIZE
            heading_run.bold = True

            # Add body content
            if len(lines) > 1:
                for bullet in lines[1:]:
                    para = doc.add_paragraph(style='ListBullet')
                    run = para.add_run(bullet.replace('- ', '').strip())
                    run.font.color.rgb = BODY_COLOR

            doc.add_paragraph()  # Add spacing between sections

 
        # (D) AZURE STORAGE UPLOAD
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
            f"document_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
        )
        
        blob_client.upload_blob(doc_buffer, overwrite=True)
        download_url = f"{blob_config['account_url']}/{blob_config['container']}/{blob_client.blob_name}?{blob_config['sas_token']}"

        # Schedule automatic deletion
        threading.Timer(300, blob_client.delete_blob).start()

        return f"Here is your Document:\n{download_url}"

    except Exception as e:
        return f"Document Generation Error: {str(e)}"



    ##################################################
    # Calling the export function
      # Based on keywords 
    ##################################################
def Call_Export(latest_question, latest_answer, chat_history, instructions):
    import re

    # Helper function: PowerPoint generation
    def generate_ppt():
        return Call_PPT(latest_question, latest_answer, chat_history, instructions)

    # Helper function: Word document generation
    def generate_doc():
        return Call_DOC(latest_question, latest_answer, chat_history, instructions)

    # Helper function: Chart generation
    def generate_chart():
        return Call_CHART(latest_question, latest_answer, chat_history, instructions)

    # Decide what to generate based on keywords in instructions
    instructions_lower = instructions.lower()

    # (A) PowerPoint / Presentation?
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

    # (B) Chart / Graph / Visualization?
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

    # (C) Document / Report / Text?
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

    # (D) Fallback if no match
    return "Not enough Information to perform export."
