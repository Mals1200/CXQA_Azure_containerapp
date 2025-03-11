import os
from flask import Flask, request, jsonify, render_template_string
from ask_func import Ask_Question
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk

load_dotenv()

app = Flask(__name__)

# Azure Configuration - Use environment variables
AZURE_SPEECH_KEY = os.getenv("SPEECH_KEY", "default-key-if-missing")
AZURE_SERVICE_REGION = os.getenv("SPEECH_REGION", "eastus")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Voice Assistant</title>
    <script src="https://aka.ms/csspeech/jsbrowserpackageraw"></script>
    <style>
        body { font-family: Segoe UI, sans-serif; max-width: 800px; margin: 20px auto; padding: 20px; }
        button { padding: 12px 24px; font-size: 16px; background: #0078d4; color: white; border: none; border-radius: 4px; }
        .status { margin: 20px 0; padding: 15px; border-radius: 5px; }
        .listening { background: #e3f2fd; border: 1px solid #2196f3; }
        .result { margin-top: 20px; padding: 15px; background: #f5f5f5; border-radius: 5px; }
    </style>
</head>
<body>
    <h1>CXQA Voice Assistant</h1>
    <button id="recordButton">Start Recording</button>
    <div id="status" class="status"></div>
    <div id="result" class="result"></div>

    <script>
        const speechConfig = SpeechSDK.SpeechConfig.fromSubscription(
            "{{ speech_key }}", 
            "{{ speech_region }}"
        );
        
        let recognizer;
        const recordButton = document.getElementById("recordButton");
        const statusDiv = document.getElementById("status");
        const resultDiv = document.getElementById("result");

        async function startRecognition() {
            recognizer = new SpeechSDK.SpeechRecognizer(speechConfig);
            
            recognizer.recognizing = (s, e) => {
                statusDiv.innerHTML = `Listening: ${e.result.text}`;
                statusDiv.className = "status listening";
            };

            recognizer.recognized = async (s, e) => {
                if (e.result.reason === SpeechSDK.ResultReason.RecognizedSpeech) {
                    statusDiv.className = "status";
                    resultDiv.innerHTML = `Processing: ${e.result.text}`;
                    
                    try {
                        const response = await fetch('/ask', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ question: e.result.text })
                        });
                        
                        const data = await response.json();
                        resultDiv.innerHTML = `
                            <strong>Question:</strong> ${e.result.text}<br>
                            <strong>Answer:</strong> ${data.answer || data.error}
                        `;
                    } catch (error) {
                        resultDiv.innerHTML = `Error: ${error.message}`;
                    }
                }
            };

            recognizer.startContinuousRecognitionAsync();
        }

        recordButton.addEventListener('click', () => {
            if (recordButton.textContent === "Start Recording") {
                recordButton.textContent = "Stop Recording";
                startRecognition();
            } else {
                recordButton.textContent = "Start Recording";
                recognizer.stopContinuousRecognitionAsync();
            }
        });
    </script>
</body>
</html>
"""

@app.route("/voice")
def voice_interface():
    return render_template_string(
        HTML_TEMPLATE,
        speech_key=AZURE_SPEECH_KEY,
        speech_region=AZURE_SERVICE_REGION
    )

@app.route("/ask", methods=["POST"])
def handle_question():
    try:
        data = request.json
        answer = Ask_Question(data["question"])
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(ssl_context='adhoc', host='0.0.0.0', port=443)
