from flask import Flask, request, jsonify, render_template_string
from ask_func import Ask_Question
import azure.cognitiveservices.speech as speechsdk

app = Flask(__name__)


AZURE_SPEECH_KEY = "DRk2PVURFbNIpb3OtRaLzOklMME1hIPhMI4fHhBxX0jwpdIHR7qtJQQJ99BCACYeBjFXJ3w3AAAYACOGIdjJ"
AZURE_SERVICE_REGION = "eastus"
ENDPOINT_URL = "cxqacontainerapp.bluesmoke-a2e4a52c.germanywestcentral.azurecontainerapps.io"

HTML_TEMPLATE = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Voice Assistant</title>
    <script src="https://aka.ms/csspeech/jsbrowserpackageraw"></script>
    <style>
        body {{ font-family: Segoe UI, sans-serif; max-width: 800px; margin: 20px auto; padding: 20px; }}
        button {{ padding: 12px 24px; font-size: 16px; background: #0078d4; color: white; border: none; border-radius: 4px; }}
        .status {{ margin: 20px 0; padding: 15px; border-radius: 5px; }}
        .listening {{ background: #e3f2fd; border: 1px solid #2196f3; }}
        .result {{ margin-top: 20px; padding: 15px; background: #f5f5f5; border-radius: 5px; }}
    </style>
</head>
<body>
    <h1>CXQA Voice Assistant</h1>
    <button id="recordButton">Start Recording</button>
    <div id="status" class="status"></div>
    <div id="result" class="result"></div>

    <script>
        const speechConfig = SpeechSDK.SpeechConfig.fromSubscription(
            "{AZURE_SPEECH_KEY}", 
            "{AZURE_SERVICE_REGION}"
        );
        
        // Configure service endpoints
        speechConfig.speechRecognitionEndpoint = "wss://{ENDPOINT_URL}/speech/recognition/conversation/cognitiveservices/v1";
        
        let recognizer;
        const recordButton = document.getElementById("recordButton");
        // ... [rest of the JavaScript code remains identical] ...
    </script>
</body>
</html>
"""

@app.route("/voice")
def voice_interface():
    return render_template_string(HTML_TEMPLATE)

@app.route("/ask", methods=["POST"])
def handle_question():
    try {
        data = request.json
        answer = Ask_Question(data["question"])
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Run with temporary self-signed certificate
    app.run(ssl_context='adhoc', host='0.0.0.0', port=443)
