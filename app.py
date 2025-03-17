import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity, ActivityTypes
from ask_func import Ask_Question, chat_history

app = Flask(__name__)

# Retrieve Bot credentials from environment variables
MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

# Setup the Bot Adapter
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Keep separate conversation histories by conversation_id
conversation_histories = {}

@app.route("/", methods=["GET"])
def home():
    # Health check route
    return jsonify({"message": "API is running!"}), 200

@app.route("/ask", methods=["POST"])
def ask():
    """
    Non-Teams usage:
      - Accepts JSON: {"question": "...", "user_email": "..."}
      - If "user_email" not provided, defaults to 'anonymous'
    """
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request, "question" is required.'}), 400

    question = data["question"]
    user_email = data.get("user_email", "anonymous")

    # Debug print
    print(f"[DEBUG] /ask endpoint called with question: {question} and user_email: {user_email}")

    ans_gen = Ask_Question(question, user_email=user_email)
    answer_text = "".join(ans_gen)

    return jsonify({"answer": answer_text})

@app.route("/api/messages", methods=["POST"])
def messages():
    """
    Route for incoming Teams Activities
    """
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body = request.json
    # Debug print
    print("[DEBUG] Received activity from Teams:", body)

    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    # Run the bot logic in an async loop
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    """
    The main logic that processes a single message from Teams
    """
    # Debug print to confirm we reached _bot_logic
    print("[DEBUG] _bot_logic invoked with text:", turn_context.activity.text)

    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Sync ask_func's chat_history with our local conversation history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    # The user's message text
    user_message = turn_context.activity.text or ""
    print(f"[DEBUG] user_message: {user_message}")

    # -----------------------------------------------------------
    # 1) Retrieve the Teams user’s email or default to "anonymous"
    # -----------------------------------------------------------
    user_email = "anonymous"
    if turn_context.activity.channel_id == "msteams":
        user_id = turn_context.activity.from_property.id
        print(f"[DEBUG] Teams user_id: {user_id}")
        try:
            member = await TeamsInfo.get_member(turn_context, user_id)
            if member and member.email:
                user_email = member.email
            elif member and member.user_principal_name:
                user_email = member.user_principal_name
            print(f"[DEBUG] user_email from Teams: {user_email}")
        except Exception as e:
            print(f"[DEBUG] Could not retrieve user email: {e}")

    # -----------------------------------------------------------
    # 2) Show the typing indicator
    # -----------------------------------------------------------
    await turn_context.send_activity(Activity(type=ActivityTypes.Typing))
    # Optional short pause so user sees the spinner
    await asyncio.sleep(1)

    # -----------------------------------------------------------
    # 3) Call Ask_Question with user_email so it logs properly
    # -----------------------------------------------------------
    print("[DEBUG] Calling Ask_Question now...")
    ans_gen = Ask_Question(user_message, user_email=user_email)
    answer_text = "".join(ans_gen)
    print(f"[DEBUG] final answer_text: {answer_text}")

    # Update conversation history
    conversation_histories[conversation_id] = ask_func.chat_history

    # -----------------------------------------------------------
    # 4) Check if there's a "Source:" in the answer for toggling
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

    # -----------------------------------------------------------
    # 5) If "Source:" was found, hide it behind a toggle
    # -----------------------------------------------------------
    if source_line:
        print("[DEBUG] Source line detected, building Adaptive Card toggle...")
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
        print("[DEBUG] Adaptive Card sent.")
    else:
        # If no "Source:" line, just send the text
        print("[DEBUG] No Source line, sending plain text message.")
        await turn_context.send_activity(Activity(type="message", text=main_answer))

if __name__ == "__main__":
    # If running on Azure, make sure your app service is on port 80 or 443
    # If local, you can do port=3978 or use ngrok to tunnel from 3978 -> a public URL
    app.run(host="0.0.0.0", port=80)
