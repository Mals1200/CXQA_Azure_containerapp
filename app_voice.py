import os
import json
import requests
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Fake credentials (replace with your actual values)
AZURE_SPEECH_KEY = "DRk2PVURFbNIpb3OtRaLzOklMME1hIPhMI4fHhBxX0jwpdIHR7qtJQQJ99BCACYeBjFXJ3w3AAAYACOGIdjJ"
AZURE_SPEECH_REGION = "eastus-fake"
BOT_ENDPOINT = "https://cxqacontainerapp.bluesmoke-a2e4a52c.germanywestcentral.azurecontainerapps.io/api/messages

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Voice Assistant</title>
    <script src="https://aka.ms/csspeech/jsbrowserpackageraw"></script>
</head>
<body>
    <button id="startButton">Start Recording</button>
    <div id="output"></div>

    <script>
        const speechConfig = SpeechSDK.SpeechConfig.fromSubscription(
            "{{ speech_key }}", 
            "{{ speech_region }}"
        );
        speechConfig.speechRecognitionLanguage = "en-US";
        
        let recognizer;
        const startBtn = document.getElementById('startButton');
        
        startBtn.addEventListener('click', () => {
            if(recognizer) recognizer.close();
            
            const audioConfig = SpeechSDK.AudioConfig.fromDefaultMicrophoneInput();
            recognizer = new SpeechSDK.SpeechRecognizer(speechConfig, audioConfig);

            recognizer.recognizeOnceAsync(result => {
                const text = result.text;
                document.getElementById('output').innerText = `Processing: ${text}`;
                
                fetch('/process', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ speech: text })
                })
                .then(response => response.json())
                .then(data => {
                    document.getElementById('output').innerHTML = `
                        <strong>Question:</strong> ${text}<br>
                        <strong>Answer:</strong> ${data.answer}
                    `;
                });
            });
        });
    </script>
</body>
</html>
"""

@app.route("/voice")
def voice_interface():
    return render_template_string(HTML_TEMPLATE,
        speech_key=AZURE_SPEECH_KEY,
        speech_region=AZURE_SPEECH_REGION
    )

@app.route("/process", methods=["POST"])
def process_speech():
    data = request.json
    response = requests.post(BOT_ENDPOINT, json={"question": data['speech"]})
    return jsonify({"answer": response.json().get("answer", "")})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
