from flask import Flask, request, jsonify
from ask_func import Ask_Question
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity

app = Flask(__name__)

# Bot Framework Adapter
SETTINGS = BotFrameworkAdapterSettings("", "")  # No credentials needed for now
ADAPTER = BotFrameworkAdapter(SETTINGS)

# New Default Route (Fixes "Not Found" issue)
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

# Existing /ask endpoint
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400
    
    question = data['question']
    answer = Ask_Question(question)
    return jsonify({'answer': answer})

# New endpoint for Bot Framework messages
@app.route('/api/messages', methods=['POST'])
def messages():
    if "application/json" not in request.headers["Content-Type"]:
        return jsonify({'error': 'Invalid content type. Expected application/json.'}), 400

    # Get the activity from the request
    activity = Activity().deserialize(request.json)
    
    # Handle the message
    if activity.type == "message":
        question = activity.text
        answer = Ask_Question(question)
        
        # Create a response activity
        response_activity = Activity(
            type="message",
            text=answer
        )
        
        # Send the response back to the Bot Framework
        return jsonify(response_activity.serialize()), 200
    
    return jsonify({'error': 'Unsupported activity type.'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)  # Ensure correct port
