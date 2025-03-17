import os
import asyncio
from flask import Flask, request, jsonify, Response

from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity, ActivityTypes

# Import your ask_func with Ask_Question and chat_history
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

# Retrieve Microsoft Bot credentials from environment
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# A dictionary to store conversation histories keyed by conversation_id
conversation_histories = {}

# 1) Health check route
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

# 2) Non-Teams usage route
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
    user_email = data.get("user_email", "anonymous")

    # Call your Ask_Question function
    ans_gen = Ask_Question(question, user_email=user_email)
    answer_text = "".join(ans_gen)
    return jsonify({"answer": answer_text})

# 3) Microsoft Teams route for incoming messages
@app.route("/api/messages", methods=["POST"])
def messages():
    """
    This route receives incoming Activities from Microsoft Teams.
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
    """
    The main logic for processing an incoming Teams message.
    """
    # Debug: confirm we reached this function
    print("[DEBUG] _bot_logic invoked!")

    # We track each conversation by ID
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Sync chat_history in ask_func with this conversation's history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""
    print(f"[DEBUG] user_message: {user_message}")

    # -----------------------------------------------------------
    # 1) Retrieve the Teams user's email or default to "anonymous"
    # -----------------------------------------------------------
    user_email = "anonymous"
    if turn_context.activity.channel_id == "msteams":
        user_id = turn_context.activity.from_property.id
        print(f"[DEBUG] user_id from Teams: {user_id}")

        try:
            member = await TeamsInfo.get_member(turn_context, user_id)
            if member and member.email:
                user_email = member.email
            elif member and member.user_principal_name:
                user_email = member.user_principal_name
            print(f"[DEBUG] user_email from Teams: {user_email}")
        except Exception as e:
            print(f"Could not retrieve user email: {e}")

    # -----------------------------------------------------------
    # 2) Show "typing" indicator, then call Ask_Question
    # -----------------------------------------------------------
    await turn_context.send_activity(Activity(type=ActivityTypes.Typing))
    await asyncio.sleep(1)  # optional short delay

    ans_gen = Ask_Question(user_message, user_email=user_email)
    answer_text = "".join(ans_gen)
    print(f"[DEBUG] final answer: {answer_text}")

    # Update the conversation's history
    conversation_histories[conversation_id] = ask_func.chat_history

    # -----------------------------------------------------------
    # 3) Check if there's "Source:" in the answer
    # -----------------------------------------------------------
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

    # If there's a "Source:" line, hide it behind a toggle
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
        # If there's no "Source:" line, just send the text
        await turn_context.send_activity(Activity(type="message", text=main_answer))

if __name__ == "__main__":
    # Make sure your environment has the correct port. 
    # If running locally with ngrok, you might do something like:
    # app.run(host="0.0.0.0", port=3978)
    app.run(host="0.0.0.0", port=80)
