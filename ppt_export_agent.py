import os
import io
import asyncio
import requests
from flask import Flask, request, jsonify, send_file
from pptx import Presentation
from pptx.util import Inches

# Import chat_history from ask_func.py
from ask_func import chat_history

app = Flask(__name__)

# Define a global variable to store the instructions
ppt_Instructions = ""

# PowerPoint generation function
def generate_ppt(chat_history_str, instructions):
    ppt = Presentation()
    
    # Title Slide
    title_slide_layout = ppt.slide_layouts[0]  # Title Slide Layout
    slide = ppt.slides.add_slide(title_slide_layout)
    title = slide.shapes.title
    subtitle = slide.placeholders[1]
    title.text = "AI Conversation Summary"
    subtitle.text = "Generated based on user interaction"
    
    # Content Slides
    content_slide_layout = ppt.slide_layouts[1]  # Title and Content Layout
    
    # Convert chat history into slides
    slides_data = chat_history_str.split('\n')
    for i in range(0, len(slides_data), 2):
        slide = ppt.slides.add_slide(content_slide_layout)
        title = slide.shapes.title
        content = slide.placeholders[1]
        
        user_text = slides_data[i] if i < len(slides_data) else ""
        bot_text = slides_data[i + 1] if i + 1 < len(slides_data) else ""
        
        title.text = f"Conversation {i//2 + 1}"
        content.text = f"User: {user_text}\n\nAssistant: {bot_text}"
    
    # Instruction Slide
    slide = ppt.slides.add_slide(content_slide_layout)
    title = slide.shapes.title
    content = slide.placeholders[1]
    title.text = "User Instructions"
    content.text = instructions
    
    # Save presentation to bytes
    ppt_bytes = io.BytesIO()
    ppt.save(ppt_bytes)
    ppt_bytes.seek(0)
    return ppt_bytes

@app.route('/export_ppt', methods=['POST'])
def export_ppt():
    global ppt_Instructions
    data = request.get_json()
    if not data or 'instructions' not in data:
        return jsonify({'error': 'Instructions are required.'}), 400
    
    ppt_Instructions = data['instructions']
    chat_history_str = "\n".join(chat_history)
    ppt_file = generate_ppt(chat_history_str, ppt_Instructions)
    
    return send_file(ppt_file, as_attachment=True, download_name="chat_history_presentation.pptx", mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
