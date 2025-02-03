from flask import Flask, request, jsonify
import requests
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext, ActivityHandler, MessageFactory
from botbuilder.integration.flask import BotFrameworkAdapterFlask

# Your Azure Container App API URL
CONTAINER_API_URL = "https://cxqacontainerapp.bluesmoke-a2e4a52c.germanywestcentral.azurecontainerapps.io/ask"

# Use your Service Principal credentials
SETTINGS = BotFrameworkAdapterSettings(
    app_id="967196cb-96e9-4413-a791-00a0a2f42877",  # Service Principal Client ID
    app_password="k5Z8Q~xd6FlsFOLH7QOQ4.96HYxCwMM~WeE70bgF"  # Service Principal Client Secret
)

# Initialize Flask App
app = Flask(__name__)

# Create Bot Adapter
ADAPTER = BotFrameworkAdapterFlask(SETTINGS)

# Bot Logic
class CxqaBot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        user_message = turn_context.activity.text  # Capture user input

        # Prepare API request payload
        payload = {"question": user_message}

        try:
            # Send request to the container API
            response = requests.post(CONTAINER_API_URL, json=payload)
            if response.status_code == 200:
                bot_reply = response.json().get("answer", "Sorry, I couldn't get an answer.")
            else:
                bot_reply = "Error fetching data from container."

        except Exception as e:
            bot_reply = f"Error: {str(e)}"

        # Send response back to Microsoft Teams
        await turn_context.send_activity(MessageFactory.text(bot_reply))

    async def on_members_added(self, members_added, turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome_message = "Hello! I'm CXQA Bot. Ask me anything!"
                await turn_context.send_activity(MessageFactory.text(welcome_message))

# Instantiate the bot
BOT = CxqaBot()

# Default Route (Health Check)
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "CXQA Bot API is running!"}), 200

# Handle incoming Teams bot messages
@app.route("/api/messages", methods=["POST"])
async def messages():
    body = request.json
    auth_header = request.headers.get("Authorization", "")
    return await ADAPTER.process_activity(body, auth_header, BOT.on_turn)

# Run Flask App
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
