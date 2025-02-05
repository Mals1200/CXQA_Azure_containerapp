from flask import Flask, request, jsonify
from ask_fun import Ask_Question

app = Flask(__name__)

# Default Route
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

# Existing Ask Route (for cURL & REST API)
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

# NEW ROUTE FOR AZURE BOT FRAMEWORK
@app.route('/api/messages', methods=['POST'])
def messages():
    data = request.get_json()

    if not data or 'type' not in data:
        return jsonify({'error': 'Invalid request'}), 400

    # Ignore event messages, only respond to messages
    if data['type'] == 'message' and 'text' in data:
        user_message = data['text']
        bot_response = Ask_Question(user_message)

        return jsonify({
            "type": "message",
            "text": bot_response
        })

    return jsonify({}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)  # Ensure correct port
