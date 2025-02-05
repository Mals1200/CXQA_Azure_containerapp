from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
import requests

class CXQABot(ActivityHandler):
    def __init__(self):
        super().__init__()

    async def on_message_activity(self, turn_context: TurnContext):
        # Get the message from the user
        user_message = turn_context.activity.text.strip()

        # Send the user message to your Flask app's /ask endpoint
        flask_api_url = "https://cxqacontainerapp.bluesmoke-a2e4a52c.germanywestcentral.azurecontainerapps.io/ask"
        response = requests.post(
            flask_api_url,
            json={"question": user_message}
        )

        # If the response is successful, get the answer and send it back to Web Chat
        if response.status_code == 200:
            data = response.json()
            answer = data.get("answer", "Sorry, I didn't understand your question.")
        else:
            answer = "Sorry, something went wrong."

        # Send the response to the user in the Web Chat
        await turn_context.send_activity(MessageFactory.text(answer))
