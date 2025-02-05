import requests
from botbuilder.core import ActivityHandler, MessageFactory, ConversationState, UserState
from botbuilder.dialogs import DialogSet, DialogTurnStatus, DialogState
from botbuilder.schema import ChannelAccount

class CXQABot(ActivityHandler):
    def __init__(self, conversation_state: ConversationState, user_state: UserState):
        if conversation_state is None:
            raise TypeError("[CXQABot]: Missing parameter. conversation_state is required but None was given")
        if user_state is None:
            raise TypeError("[CXQABot]: Missing parameter. user_state is required but None was given")

        self.conversation_state = conversation_state
        self.user_state = user_state

        # Initialize DialogSet with a valid DialogState
        self.dialog_state = self.conversation_state.create_property("DialogState")
        self.dialogs = DialogSet(self.dialog_state)

    async def on_message_activity(self, turn_context):
        # Retrieve the dialog context
        dialog_context = await self.dialogs.create_context(turn_context)

        # Check if there is an active dialog
        if dialog_context.active_dialog:
            # Continue the active dialog
            await dialog_context.continue_dialog()
        else:
            # Start a new dialog or handle the message
            await self.handle_message(turn_context)

        # Save any state changes that might have occurred during the turn
        await self.conversation_state.save_changes(turn_context)
        await self.user_state.save_changes(turn_context)

    async def handle_message(self, turn_context):
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
