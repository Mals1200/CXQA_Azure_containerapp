from flask import Flask, request, jsonify
from ask_fun import Ask_Question
import os

app = Flask(__name__)

# Default Route (for health checks)
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

# Existing Ask Route
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

# Azure Bot Framework Integration
@app.route('/api/messages', methods=['POST'])
def messages():
    data = request.get_json()

    if not data or 'type' not in data:
        return jsonify({'error': 'Invalid request'}), 400

    # Process only message type
    if data['type'] == 'message' and 'text' in data:
        user_message = data['text']
        bot_response = Ask_Question(user_message)

        return jsonify({
            "type": "message",
            "text": bot_response
        })

    return jsonify({}), 200

if __name__ == '__main__':
    # Use Azure's default port 8080
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
