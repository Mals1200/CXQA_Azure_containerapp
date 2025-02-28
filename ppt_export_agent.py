# ppt_export_agent.py
import io
import requests
from flask import Blueprint, request, jsonify, send_file
from datetime import datetime
from pptx import Presentation
from ask_func import chat_history

ppt_export_bp = Blueprint("ppt_export_bp", __name__)

LLM_ENDPOINT = (
    "https://cxqaazureaihub2358016269.openai.azure.com/"
    "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
)
LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

def generate_ppt_outline(chat_history_str, instructions):
    system_prompt = f"""
You are a PowerPoint presentation expert. Use the following information to make the slides.
Rules:
- Only use the following information to create the slides.
- Don't come up with anything outside of your scope in your slides.
- Your output will be utilized by the "python-pptx" to create the slides.

(The Information)
Conversation:
{chat_history_str}

User_Instructions:
{instructions}
"""
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Create the PowerPoint outline now."}
        ],
        "max_tokens": 1000,
        "temperature": 0.7
    }
    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }
    try:
        resp = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Error: {e}"

def build_ppt_from_outline(outline_text):
    prs = Presentation()
    slide_sections = outline_text.split("\n\n")

    for sec in slide_sections:
        layout = prs.slide_layouts[1]  # Title + Content
        slide = prs.slides.add_slide(layout)
        shapes = slide.shapes
        title_shape = shapes.title
        body_shape = shapes.placeholders[1]

        lines = sec.strip().split("\n")
        if lines:
            title_shape.text = lines[0]
        bullet_lines = lines[1:]

        tf = body_shape.text_frame
        for bullet in bullet_lines:
            p = tf.add_paragraph()
            p.text = bullet
            p.level = 0

    ppt_buffer = io.BytesIO()
    prs.save(ppt_buffer)
    ppt_buffer.seek(0)
    return ppt_buffer

@ppt_export_bp.route("/create_ppt", methods=["POST"])
def create_ppt():
    data = request.get_json()
    instructions = data.get("instructions", "").strip()
    if not instructions:
        return jsonify({"error": "No instructions provided."}), 400

    conversation_str = "\n".join(chat_history)
    outline = generate_ppt_outline(conversation_str, instructions)
    ppt_file = build_ppt_from_outline(outline)

    return send_file(
        ppt_file,
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        as_attachment=True,
        download_name=f"Generated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
    )
