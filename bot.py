import requests
from botbuilder.core import ActivityHandler, MessageFactory
from botbuilder.dialogs import DialogSet, DialogTurnStatus
from botbuilder.dialogs import WaterfallDialog, WaterfallStepContext

class CXQABot(ActivityHandler):
    def __init__(self):
        self.dialogs = DialogSet()

    async def on_message_activity(self, turn_context):
        question = turn_context.activity.text
        answer = await self.ask_container_app(question)
        await turn_context.send_activity(MessageFactory.text(answer))

    async def ask_container_app(self, question):
        url = "https://cxqacontainerapp.bluesmoke-a2e4a52c.germanywestcentral.azurecontainerapps.io/ask"
        headers = {"Content-Type": "application/json"}
        data = {"question": question}
        
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            return response.json().get('answer')
        else:
            return "Error: Unable to get a response from the container app."
