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
    """
    Main Bot logic that:
     1. Retrieves or initializes conversation history by conversation_id.
     2. Extracts the user message.
     3. Attempts to retrieve the user's email from claims (otherwise 'anonymous').
     4. Calls Ask_Question(...) passing the user_id.
     5. Sends the adaptive card with hidden "Source" & "Code" toggles if available.
     6. Updates conversation history.
    """
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # ----------------------------------------------
    # Extract user email from the activity claims
    # ----------------------------------------------
    user_id = "anonymous"
    for claim in (turn_context.activity.claims or []):
        if claim.get("type") == "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress":
            user_id = claim.get("value")
            break

    # 1) built-in Teams typing indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # 2) get the answer from your function
    ans_gen = Ask_Question(user_message, user_id=user_id)
    answer_text = "".join(ans_gen)

    # 3) update conversation history
    conversation_histories[conversation_id] = ask_func.chat_history

    # 4) If there's "Source:" in answer_text, parse out main answer + source lines
    import re
    source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)
    if match:
        main_answer = match.group(1).strip()
        source_line = match.group(2).strip()
        appended_details = match.group(3) if match.group(3) else ""
    else:
        main_answer = answer_text
        source_line = ""
        appended_details = ""

    # 5) If source_line is present, build an Adaptive Card so user can toggle to reveal/hide source code
    if source_line:
        body_blocks = [
            {
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            },
            {
                "type": "TextBlock",
                "text": source_line,
                "wrap": True,
                "id": "sourceLineBlock",
                "isVisible": False
            }
        ]

        if appended_details:
            body_blocks.append({
                "type": "TextBlock",
                "text": appended_details.strip(),
                "wrap": True,
                "id": "sourceBlock",
                "isVisible": False
            })

        actions = []
        if appended_details or source_line:
            actions = [
                {
                    "type": "Action.ToggleVisibility",
                    "title": "Show Source",
                    "targetElements": ["sourceLineBlock", "sourceBlock"]
                }
            ]

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": body_blocks,
            "actions": actions,
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }
        message = Activity(
            type="message",
            attachments=[{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": adaptive_card
            }]
        )
        await turn_context.send_activity(message)
    else:
        # 6) Otherwise, just send the plain text
        await turn_context.send_activity(Activity(type="message", text=main_answer))

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body = request.json
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, ssl_context="adhoc")
