from flask import Flask, request, jsonify
from ask_func import Ask_Question

# 1) BOT FRAMEWORK IMPORTS
import os
from botbuilder.core import BotFrameworkAdapter
from botbuilder.schema import Activity

app = Flask(__name__)

# 2) READ BOT CREDENTIALS FROM ENV
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Create the adapter with these credentials
adapter = BotFrameworkAdapter(
    app_id=MICROSOFT_APP_ID,
    app_password=MICROSOFT_APP_PASSWORD
)

# 3) EXISTING ENDPOINTS
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

# 4) ADD BOT ENDPOINT: /api/messages
@app.route("/api/messages", methods=["POST"])
def messages():
    # The Bot sends a JSON payload of type 'Activity'
    if "application/json" not in request.headers.get("Content-Type", ""):
        return jsonify({"error": "Invalid request type"}), 400

    body = request.json
    activity = Activity().deserialize(body)

    # The user's typed message from Web Chat
    user_message = activity.text or ""

    # Use your existing logic to get an answer
    answer = Ask_Question(user_message)

    # Format a response Activity for Bot
    response_activity = Activity(
        type="message",
        text=answer
    )

    # Send the activity back to the user
    return adapter.send_activities(request, [response_activity])

# 5) ENTRY POINT FOR GUNICORN
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
