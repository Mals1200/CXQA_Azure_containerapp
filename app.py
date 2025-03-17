import os
import asyncio

from flask import Flask, request, jsonify, Response

# Important Bot Builder imports
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

# Import your QnA / logic code
from ask_func import Ask_Question, chat_history


# ------------------------------------------------
#   Flask App and Bot Adapter Setup
# ------------------------------------------------
app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# In-memory storage for conversation history per conversation ID
conversation_histories = {}


# ------------------------------------------------
#   Basic Route
# ------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200


# ------------------------------------------------
#   Optional: Simple /ask endpoint for local tests
# ------------------------------------------------
@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request, "question" is required.'}), 400

    question = data["question"]

    # In this example, we’re NOT capturing user_id from outside, so it remains "anonymous"
    ans_gen = Ask_Question(question, user_id="anonymous")
    answer_text = "".join(ans_gen)
    return jsonify({"answer": answer_text})


# ------------------------------------------------
#   Teams-compatible /api/messages endpoint
# ------------------------------------------------
@app.route("/api/messages", methods=["POST"])
def messages():
    # Must check JSON content
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body = request.json
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    # Run the Bot's logic in an event loop
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)


# ------------------------------------------------
#   Main Bot Logic (called by adapter)
# ------------------------------------------------
async def _bot_logic(turn_context: TurnContext):
    """
    This function is invoked each time a message arrives from Teams.
    We show a "typing" indicator, gather user ID from Teams, and
    then get an answer using the Ask_Question function.
    """

    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    # Let ask_func.py see this conversation's history
    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # --------------------------------------------------------------------------
    # 1) EXTRACT USER EMAIL / UPN FROM TEAMS (OR FALL BACK IF NOT FOUND)
    # --------------------------------------------------------------------------
    user_id = "anonymous"

    from_prop = turn_context.activity.from_property
    channel_data = turn_context.activity.channel_data or {}

    # (a) If there's a typical "teamsUser" in channelData
    teams_user = channel_data.get("teamsUser", {})
    if isinstance(teams_user, dict):
        possible_upn = teams_user.get("userPrincipalName")
        if possible_upn and "@" in possible_upn:
            user_id = possible_upn

    # (b) If not found, check from_property
    if user_id == "anonymous" and from_prop:
        # user_principal_name if available
        if hasattr(from_prop, "user_principal_name") and from_prop.user_principal_name:
            user_id = from_prop.user_principal_name

        # or try additional_properties
        elif getattr(from_prop, "additional_properties", None):
            extra_props = from_prop.additional_properties
            upn_extra = extra_props.get("userPrincipalName") or extra_props.get("email")
            if upn_extra and "@" in upn_extra:
                user_id = upn_extra

        # If still not found, at least try aadObjectId
        if user_id == "anonymous" and hasattr(from_prop, "aadObjectId") and from_prop.aadObjectId:
            user_id = from_prop.aadObjectId

        # Fallback to from_prop.id
        if user_id == "anonymous" and from_prop.id:
            user_id = from_prop.id

    # --------------------------------------------------------------------------
    # 2) Show "thinking/typing" indicator
    # --------------------------------------------------------------------------
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # --------------------------------------------------------------------------
    # 3) Get answer from ask_func
    # --------------------------------------------------------------------------
    ans_gen = Ask_Question(user_message, user_id=user_id)
    answer_text = "".join(ans_gen)

    # --------------------------------------------------------------------------
    # 4) Save updated conversation history for the user
    # --------------------------------------------------------------------------
    conversation_histories[conversation_id] = ask_func.chat_history

    # --------------------------------------------------------------------------
    # 5) (Optional) Build an Adaptive Card if you have "Source:" lines
    #    Otherwise, just send plain text.
    # --------------------------------------------------------------------------
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
        # We can hide the source line and appended details behind a toggle
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
        # No "Source:" line, just return the plain text
        await turn_context.send_activity(Activity(type="message", text=main_answer))


# ------------------------------------------------
#   Entry point for local run
# ------------------------------------------------
if __name__ == "__main__":
    # Expose on port 80 by default (or pick another)
    app.run(host="0.0.0.0", port=80)
