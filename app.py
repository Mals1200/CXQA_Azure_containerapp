from flask import Flask, request, jsonify
from ask_func import Ask_Question
from bot import CXQABot  # Import the bot class

app = Flask(__name__)

# Initialize the bot
bot = CXQABot()

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

@app.route('/bot', methods=['POST'])  # Endpoint for the bot
def bot_endpoint():
    # Logic to handle bot messages
    return bot.on_message_activity(request)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
