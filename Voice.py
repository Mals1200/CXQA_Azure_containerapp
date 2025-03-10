import sys
import azure.cognitiveservices.speech as speechsdk
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel
)
from PyQt5.QtCore import Qt

# Import your ask_func.py module's function:
# Make sure ask_func.py is in the same directory.
from ask_func import Ask_Question

# Replace these with your actual Azure Speech key and region.
AZURE_SPEECH_KEY = "DRk2PVURFbNIpb3OtRaLzOklMME1hIPhMI4fHhBxX0jwpdIHR7qtJQQJ99BCACYeBjFXJ3w3AAAYACOGIdjJ"
AZURE_SERVICE_REGION = "eastus"


class VoiceApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Assistant")

        # Main layout
        self.central_widget = QWidget()
        self.layout = QVBoxLayout(self.central_widget)
        self.setCentralWidget(self.central_widget)

        # Labels to show question and answer
        self.question_label = QLabel("Question:", self)
        self.answer_label = QLabel("Answer:", self)
        self.question_label.setWordWrap(True)
        self.answer_label.setWordWrap(True)

        # Record button
        self.record_button = QPushButton("Record", self)
        self.record_button.clicked.connect(self.handle_record)

        # Add widgets to layout
        self.layout.addWidget(self.record_button)
        self.layout.addWidget(self.question_label)
        self.layout.addWidget(self.answer_label)

    def handle_record(self):
        """Handles recording via microphone and converting speech to text."""
        # Create a speech configuration
        speech_config = speechsdk.SpeechConfig(
            subscription=AZURE_SPEECH_KEY,
            region=AZURE_SERVICE_REGION
        )

        # Set up the recognizer
        audio_config = speechsdk.AudioConfig(use_default_microphone=True)
        speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config
        )

        # Start speech recognition
        self.question_label.setText("Question: (listening...)")
        self.answer_label.setText("Answer:")

        result = speech_recognizer.recognize_once_async().get()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            recognized_text = result.text
            self.question_label.setText(f"Question: {recognized_text}")

            # Send the recognized text to Ask_Question from ask_func.py
            answer = Ask_Question(recognized_text)

            # Display the answer
            self.answer_label.setText(f"Answer: {answer}")

        elif result.reason == speechsdk.ResultReason.NoMatch:
            self.question_label.setText("Question: (No speech could be recognized)")
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            self.question_label.setText(f"Question: (Canceled: {cancellation_details.reason})")


def main():
    app = QApplication(sys.argv)
    window = VoiceApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
