import os
from flask import Flask, request, render_template_string
import azure.cognitiveservices.speech as speechsdk
from ask_func import Ask_Question

app = Flask(__name__)

# Retrieve Azure Bot Service credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Retrieve Azure Speech credentials
SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "YOUR_SPEECH_KEY_HERE")
SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")

# HTML template with recording UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Voice Assistant</title>
</head>
<body>
    <h1>Voice Assistant</h1>
    <button id="recordBtn" onclick="toggleRecording()">Start Recording</button>
    <p id="status">Status: Idle</p>

    <form id="uploadForm" method="POST" enctype="multipart/form-data" style="display:none;">
        <input type="file" name="audio_data" id="audioFile" />
    </form>

    <h3>Question:</h3>
    <p id="question">{{ question if question else "" }}</p>
    <h3>Answer:</h3>
    <p id="answer">{{ answer if answer else "" }}</p>

<script>
let mediaRecorder;
let audioChunks = [];

function toggleRecording() {
    if (!mediaRecorder || mediaRecorder.state === "inactive") {
        startRecording();
    } else {
        stopRecording();
    }
}

function startRecording() {
    document.getElementById('status').innerText = "Status: Recording...";
    navigator.mediaDevices.getUserMedia({ audio: true })
        .then(stream => {
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            mediaRecorder.ondataavailable = event => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };

            mediaRecorder.onstop = async () => {
                document.getElementById('status').innerText = "Status: Uploading...";
                const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                
                const file = new File([audioBlob], "recording.wav", { type: 'audio/wav' });
                const formData = new FormData();
                formData.append('audio_data', file);

                fetch('/voice', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.text())
                .then(html => {
                    document.open();
                    document.write(html);
                    document.close();
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('status').innerText = "Status: Error uploading audio.";
                });
            };

            mediaRecorder.start();
        })
        .catch(error => {
            console.error('Error accessing microphone', error);
            document.getElementById('status').innerText = "Status: Cannot access microphone.";
        });
}

function stopRecording() {
    document.getElementById('status').innerText = "Status: Stopping...";
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
}
</script>
</body>
</html>
"""

@app.route('/voice', methods=['GET', 'POST'])
def voice_assistant():
    question = None
    answer = None

    if request.method == 'POST' and 'audio_data' in request.files:
        audio_file = request.files['audio_data']
        temp_path = "temp.wav"
        audio_file.save(temp_path)

        # Configure Azure Speech
        speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
        audio_input = speechsdk.AudioConfig(filename=temp_path)
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_input)

        # Recognize speech
        result = recognizer.recognize_once_async().get()
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            question = result.text
            answer = "".join(Ask_Question(question))
        else:
            question = "No speech recognized or an error occurred."
            answer = ""

        os.remove(temp_path)  # Clean up temp file

    return render_template_string(HTML_TEMPLATE, question=question, answer=answer)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3979, debug=True)
