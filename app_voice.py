import os
import asyncio
from flask import Flask, request, jsonify, render_template_string
import azure.cognitiveservices.speech as speechsdk
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from ask_func import Ask_Question

app = Flask(__name__)

# Retrieve Azure Bot credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Azure Speech Credentials
SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "YOUR_SPEECH_KEY_HERE")
SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")

conversation_histories = {}

# HTML UI for Voice Assistant
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

        speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
        audio_input = speechsdk.AudioConfig(filename=temp_path)
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_input)

        result = recognizer.recognize_once_async().get()
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            question = result.text.strip()
            answer = "".join(Ask_Question(question))
        else:
            question = "Speech not recognized."
            answer = "I couldn't understand you. Please try again."

        os.remove(temp_path)

    return render_template_string(HTML_TEMPLATE, question=question, answer=answer)

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return jsonify({"error": "Invalid content type"}), 415

    body = request.json
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return jsonify({"status": "Message received"}), 200

async def _bot_logic(turn_context: TurnContext):
    user_message = turn_context.activity.text or ""

    if not user_message.strip():
        await turn_context.send_activity("I didn't understand that.")
        return

    ans_gen = Ask_Question(user_message)
    answer_text = "".join(ans_gen)

    if not answer_text.strip():
        answer_text = "I couldn't find an answer."

    await turn_context.send_activity(Activity(type="message", text=answer_text))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
