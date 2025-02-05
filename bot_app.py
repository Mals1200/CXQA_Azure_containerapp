from flask import Flask
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity
from bot import CXQABot

app = Flask(__name__)

# Set up Bot Framework adapter
settings = BotFrameworkAdapterSettings(
    app_id="your-bot-app-id", 
    app_password="your-bot-app-password"
)
adapter = BotFrameworkAdapter(settings)
bot = CXQABot()

@app.route("/api/messages", methods=["POST"])
def messages():
    # Process incoming requests and pass them to the Bot Framework adapter
    activity = Activity().deserialize(request.json)
    response = adapter.process_activity(activity, "", bot.on_turn)
    return response


if __name__ == "__main__":
    app.run(port=3978)
