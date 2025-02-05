from flask import Flask, request, jsonify
from ask_fun import Ask_Question  # Ensure this points to the correct logic

app = Flask(__name__)

# Route for health check
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

# Route to ask questions to the bot
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    question = data['question']
    answer = Ask_Question(question)  # Calls your function for getting a response
    return jsonify({'answer': answer})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)  # Ensure the container exposes port 80
