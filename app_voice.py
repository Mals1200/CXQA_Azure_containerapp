import os
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Azure Speech Configuration (From your screenshot)
SPEECH_KEY = "DASZPVLJFKpMzpbXDFkAuCQwDMZTHlHM4IehdaGlyapdHIKTqrQQWBCACvRgFU3vAAAAwKCQsfg"
SPEECH_REGION = "East US"  # From Location field
BOT_ENDPOINT = "http://cxgacontainerapp:80/ask"  # Your container app endpoint

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>CXQA Voice Assistant</title>
    <script src="https://aka.ms/csspeech/jsbrowserpackageraw"></script>
</head>
<body>
    <button id="startBtn">Start Speaking</button>
    <div id="responseContainer" style="margin-top: 20px;"></div>

    <script>
        const speechConfig = SpeechSDK.SpeechConfig.fromSubscription(
            "{{ speech_key }}", 
            "{{ speech_region }}"
        );
        speechConfig.speechRecognitionLanguage = "en-US";
        
        let recognizer;
        
        document.getElementById('startBtn').addEventListener('click', () => {
            const audioConfig = SpeechSDK.AudioConfig.fromDefaultMicrophoneInput();
            recognizer = new SpeechSDK.SpeechRecognizer(speechConfig, audioConfig);

            recognizer.recognizeOnceAsync(result => {
                const text = result.text;
                fetch('/process', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text: text })
                })
                .then(response => response.json())
                .then(data => {
                    document.getElementById('responseContainer').innerHTML = `
                        <strong>You asked:</strong> ${text}<br>
                        <strong>Answer:</strong> ${data.answer}
                    `;
                });
            });
        });
    </script>
</body>
</html>
'''

@app.route("/voice")
def voice_interface():
    return render_template_string(HTML_TEMPLATE,
        speech_key=SPEECH_KEY,
        speech_region=SPEECH_REGION
    )

@app.route("/process", methods=["POST"])
def process_speech():
    user_input = request.json.get('text', '')
    bot_response = requests.post(BOT_ENDPOINT, json={"question": user_input})
    return jsonify({"answer": bot_response.json().get("answer", "")})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
