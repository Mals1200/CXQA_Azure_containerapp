import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter, 
    BotFrameworkAdapterSettings, 
    TurnContext
)
from botbuilder.schema import Activity, ActivityTypes
from botbuilder.core.teams import TeamsInfo
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

# Azure Bot Service Credentials
MICROSOFT_APP_ID = "YOUR_BOT_APP_ID"
MICROSOFT_APP_PASSWORD = "YOUR_BOT_PASSWORD"

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

conversation_histories = {}

async def _bot_logic(turn_context: TurnContext):
    """Enhanced Teams integration with user email extraction"""
    # Get user email from Teams
    user_email = "anonymous"
    try:
        teams_member = await TeamsInfo.get_member(
            turn_context, 
            turn_context.activity.from_property.id
        )
        user_email = teams_member.email or teams_member.user_principal_name
    except Exception as e:
        print(f"Error getting user email: {e}")

    # Process message with user email
    ans_gen = Ask_Question(
        turn_context.activity.text, 
        user_id=user_email
    )
    answer_text = "".join(ans_gen)

    # Send response back to Teams
    await turn_context.send_activity(
        Activity(type=ActivityTypes.message, text=answer_text)
    
    # Maintain conversation history
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    conversation_histories[conversation_id].append({
        "user": user_email,
        "message": turn_context.activity.text,
        "response": answer_text
    })

@app.route("/api/messages", methods=["POST"])
async def messages():
    """Enhanced message handler for Teams"""
    if "application/json" not in request.headers["Content-Type"]:
        return Response(status=415)

    activity = Activity().deserialize(request.json)
    auth_header = request.headers["Authorization"]

    await adapter.process_activity(
        activity, 
        auth_header, 
        _bot_logic
    )
    return Response(status=200)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, ssl_context="adhoc")
