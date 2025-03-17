import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from ask_func import Ask_Question, chat_history  # updated ask_func, which logs user_email

# 1) Import TeamsInfo to retrieve user emails
from botbuilder.core.teams import TeamsInfo

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

conversation_histories = {}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/ask", methods=["POST"])
def ask():
    """
    This route is for non-Teams usage.
    If the caller provides 'user_email' in JSON, we will pass it to Ask_Question().
    Otherwise default to 'anonymous'.
    """
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request, "question" is required.'}), 400

    question = data["question"]
    # if they included a user_email, use it, else "anonymous"
    user_email = data.get("user_email", "anonymous")

    ans_gen = Ask_Question(question, user_email=user_email)
    answer_text = "".join(ans_gen)
    return jsonify({"answer": answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    This route is for Microsoft Teams messages.
    """
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

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # -----------------------------------------------------------
    # 2) Retrieve the Teams user's email or default to "anonymous"
    # -----------------------------------------------------------
    user_email = "anonymous"
    if turn_context.activity.channel_id == "msteams":
        # Get the user's Teams ID
        user_id = turn_context.activity.from_property.id
        try:
            # Attempt to get more info (including email) via TeamsInfo
            member = await TeamsInfo.get_member(turn_context, user_id)
            if member and member.email:
                user_email = member.email
            elif member and member.user_principal_name:
                user_email = member.user_principal_name
        except Exception as e:
            # If we fail (permissions or otherwise), leave it as "anonymous"
            print(f"Could not retrieve user email: {e}")

    # -----------------------------------------------------------
    # 3) Pass user_email to Ask_Question so it gets logged
    # -----------------------------------------------------------
    ans_gen = Ask_Question(user_message, user_email=user_email)
    answer_text = "".join(ans_gen)

    # Update the conversation history
    conversation_histories[conversation_id] = ask_func.chat_history

    # If there's "Source:" in answer_text, parse out main answer, source line, and appended details if any
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

    if source_line:
        # Hide the source line and appended details behind a toggle in an Adaptive Card
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
        # If there's no "Source:", just send a normal text response
        await turn_context.send_activity(Activity(type="message", text=main_answer))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
