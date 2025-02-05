from flask import Flask, request, jsonify
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity
from bot import CXQABot  # Import your bot class

app = Flask(__name__)

# Set up Bot Framework Adapter
adapter_settings = BotFrameworkAdapterSettings(
    app_id="your-bot-app-id",  # This is still required
    app_password=None  # No need for the app password since we’re using OAuth2
)
adapter = BotFrameworkAdapter(adapter_settings)

# Initialize the bot
bot = CXQABot()

@app.route("/api/messages", methods=["POST"])
def messages():
    # Deserialize the incoming activity (message from Web Chat)
    activity = Activity().deserialize(request.json)
    
    # Process the activity using the adapter and bot
    response = adapter.process_activity(activity, "", bot.on_turn)
    
    # Return the response
    return response

# Home route to check if the Flask API is running
@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'API is running!'}), 200

# Route for your Flask API to handle questions
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': 'Invalid request, "question" field is required.'}), 400

    question = data['question']

    # Use the bot logic directly here
    answer = bot.on_message_activity(Activity(text=question, type="message"))
    
    return jsonify({'answer': answer})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)  # Ensure Flask app runs on port 80
