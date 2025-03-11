import os
from flask import Flask, request, render_template_string
import azure.cognitiveservices.speech as speechsdk
# Import your Ask_Question function
from ask_func import Ask_Question

app = Flask(__name__)

# =========================
# Azure Speech Credentials
# =========================
SPEECH_KEY = "DRk2PVURFbNIpb3OtRaLzOklMME1hIPhMI4fHhBxX0jwpdIHR7qtJQQJ99BCACYeBjFXJ3w3AAAYACOGIdjJ"
SPEECH_REGION = "eastus"
SPEECH_ENDPOINT = "https://eastus.api.cognitive.microsoft.com/"

# =========================
# HTML template
# =========================
# This template includes a button to start/stop recording using the MediaRecorder API.
# It then sends the audio to the server for transcription and displays the recognized text + answer.
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
                
                // Convert blob to File
                const file = new File([audioBlob], "recording.wav", { type: 'audio/wav' });

                // Create FormData and append
                const formData = new FormData();
                formData.append('audio_data', file);

                // Send the POST request
                fetch('/voice', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.text())
                .then(html => {
                    // Replace the entire body with new HTML (quick way).
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

    if request.method == 'POST':
        # Handle uploaded audio
        if 'audio_data' in request.files:
            audio_file = request.files['audio_data']
            temp_path = "temp.wav"
            audio_file.save(temp_path)

            # Setup Azure Speech
            speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, endpoint=SPEECH_ENDPOINT)
            # You could also do: speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
            audio_input = speechsdk.AudioConfig(filename=temp_path)
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_input)

            # Recognize once
            result = speech_recognizer.recognize_once_async().get()
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                question = result.text
                # Call your custom Q&A function
                answer = Ask_Question(question)
            else:
                question = "No speech recognized or an error occurred."
                answer = ""

            # Clean up temp file if you like
            os.remove(temp_path)

    return render_template_string(HTML_TEMPLATE, question=question, answer=answer)

if __name__ == "__main__":
    # IMPORTANT: If you’re running this standalone, you can do:
    app.run(host='0.0.0.0', port=3978, debug=True)
