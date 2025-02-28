# app.py (partial) - Example of hooking up ppt_export_agent

import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Existing imports:
from ask_func import Ask_Question, chat_history
import ppt_export_agent  # <-- Import our new PPT agent

app = Flask(__name__)

# ... your existing BotFramework setup ...
adapter_settings = BotFrameworkAdapterSettings("", "")
adapter = BotFrameworkAdapter(adapter_settings)

conversation_histories = {}  # to track chat history per conversation

@app.route("/api/messages", methods=["POST"])
def messages():
    # ... existing code ...
    pass

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Sync up with ask_func's chat_history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # The user's message
    user_message = (turn_context.activity.text or "").strip()

    ############################################
    # 1) DETECT if user explicitly says "export ppt"
    ############################################
    if user_message.lower() == "export ppt":
        # Step 1: Prompt user for instructions
        await turn_context.send_activity("Please type your PPT instructions now.")
        
        # Let's store a marker in the conversation history 
        # so we know the next message is the 'instructions'
        ask_func.chat_history.append("Assistant: [AWAITING_PPT_INSTRUCTIONS]")
        conversation_histories[conversation_id] = ask_func.chat_history
        return

    # Step 2: If we are waiting for PPT instructions, the next user message is the instructions
    if "Assistant: [AWAITING_PPT_INSTRUCTIONS]" in ask_func.chat_history:
        # Remove the marker from chat history
        ask_func.chat_history = [x for x in ask_func.chat_history if "[AWAITING_PPT_INSTRUCTIONS]" not in x]
        
        ppt_instructions = user_message
        
        # 3) Actually call the ppt_export_agent!
        download_link = ppt_export_agent.export_ppt(
            ask_func.chat_history,   # entire history
            ppt_instructions         # user instructions
        )
        
        # 4) Provide user a button or text link to download
        # Example: an Adaptive Card
        adaptive_card = {
            "type": "AdaptiveCard",
            "body": [
                {"type": "TextBlock", "text": "Here is your PowerPoint download link:", "wrap": True}
            ],
            "actions": [
                {
                    "type": "Action.OpenUrl",
                    "title": "Export",
                    "url": download_link
                }
            ],
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }

        message = Activity(
            type="message",
            attachments=[{"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card}]
        )
        await turn_context.send_activity(message)

        # Save updated history and return
        conversation_histories[conversation_id] = ask_func.chat_history
        return

    ############################################
    # 2) Otherwise, do normal Q&A with ask_func
    ############################################
    answer = Ask_Question(user_message)
    conversation_histories[conversation_id] = ask_func.chat_history

    if "\n\nSource:" in answer:
        # ... your existing code to show source ...
        pass
    else:
        await turn_context.send_activity(Activity(type="message", text=answer))
