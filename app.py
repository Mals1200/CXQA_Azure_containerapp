import os
import asyncio
import logging

from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity

# ——— Your Q&A logic imported once at startup ——————————————————————————
import ask_func
from ask_func import Ask_Question, chat_history

# ——— Flask + Bot adapter setup —————————————————————————————————————————
app = Flask(__name__)

MICROSOFT_APP_ID       = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# ——— Per‑conversation histories stored in memory —————————————————————————
conversation_histories = {}

# ——— Configure logging ——————————————————————————————————————————————
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200


@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body        = request.json
    activity    = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)


async def _bot_logic(turn_context: TurnContext):
    # 1) Fetch or init this conversation's history
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []
    # Point ask_func.chat_history at our per‑conversation list
    ask_func.chat_history = conversation_histories[conversation_id]

    # 2) Extract incoming text
    user_message = turn_context.activity.text or ""
    if not user_message.strip():
        return

    # 3) Determine a stable user_id via TeamsInfo
    user_id = "anonymous"
    try:
        teams_user_id = turn_context.activity.from_property.id
        member = await TeamsInfo.get_member(turn_context, teams_user_id)
        if getattr(member, "user_principal_name", None):
            user_id = member.user_principal_name
        elif getattr(member, "email", None):
            user_id = member.email
        else:
            user_id = teams_user_id
    except Exception as e:
        logger.warning(f"Could not resolve Teams user identity: {e}")
        user_id = turn_context.activity.from_property.id or "anonymous"

    # 4) Show typing indicator
    await turn_context.send_activity(Activity(type="typing"))

    # 5) Generate answer
    answer_text = ""
    try:
        for chunk in Ask_Question(
            question=user_message,
            user_id=user_id
        ):
            answer_text += str(chunk)
    except Exception as e:
        logger.error(f"Error in Ask_Question: {e}", exc_info=True)
        answer_text = f"I'm sorry—something went wrong. ({e})"

    # 6) Persist updated history back into our dict
    conversation_histories[conversation_id] = ask_func.chat_history

    # 7) If the answer contains a “Source:” line, render it as an Adaptive Card toggle
    import re
    source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)

    if match:
        main_answer      = match.group(1).strip()
        source_line      = match.group(2).strip()
        appended_details = match.group(3).strip() if match.group(3) else ""

        body = [
            {"type": "TextBlock", "text": main_answer, "wrap": True},
            {
                "type": "TextBlock",
                "text": source_line,
                "wrap": True,
                "id": "sourceLineBlock",
                "isVisible": False,
            },
        ]
       
