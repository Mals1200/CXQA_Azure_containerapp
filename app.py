# Version 5

import os
import asyncio
import logging
import re

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapterSettings,
    BotFrameworkAdapter,
    ConversationState,
    MemoryStorage,
    TurnContext,
)
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity

from ask_func import Ask_Question

# ——— Configure logging ——————————————————————————————————————————————
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Flask + Bot adapter setup —————————————————————————————————————————
app = Flask(__name__)

MICROSOFT_APP_ID       = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# ——— ConversationState (swap MemoryStorage for Cosmos/Blob in production) ———————
memory             = MemoryStorage()
conversation_state = ConversationState(memory)
adapter.use(conversation_state)

# property for per‑conversation history
CONV_HISTORY_PROPERTY = conversation_state.create_property("ConversationHistory")


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
        loop.run_until_complete(
            adapter.process_activity(activity, auth_header, _bot_logic)
        )
    finally:
        loop.close()

    return Response(status=200)


async def _bot_logic(turn_context: TurnContext):
    # 1) Load or init conversation history list
    history = await CONV_HISTORY_PROPERTY.get(turn_context, [])

    user_message = turn_context.activity.text or ""
    if not user_message.strip():
        return

    # 2) Resolve user identity robustly
    user_id = await get_user_identity(turn_context)

    # 3) Show typing indicator
    await turn_context.send_activity(Activity(type="typing"))

    # 4) Call your Ask_Question, passing in history and a fresh cache
    answer_text = ""
    try:
        for chunk in Ask_Question(
            question=user_message,
            user_id=user_id,
            chat_history=history,
            tool_cache={}
        ):
            answer_text += str(chunk)
    except Exception as e:
        logger.error(f"Error in Ask_Question: {e}", exc_info=True)
        answer_text = f"I'm sorry—something went wrong. ({e})"

    # 5) Persist updated history
    await CONV_HISTORY_PROPERTY.set(turn_context, history)
    await conversation_state.save_changes(turn_context)

    # 6) Send the reply (handles long messages + adaptive‑card toggle)
    await send_formatted_response(turn_context, answer_text)


async def get_user_identity(turn_context: TurnContext) -> str:
    """Extract the most reliable user ID in Teams."""
    user_id = "anonymous"
    try:
        teams_user_id = turn_context.activity.from_property.id
        try:
            member = await TeamsInfo.get_member(turn_context, teams_user_id)
            # prefer UPN, then email, then Teams ID
            if getattr(member, "user_principal_name", None):
                user_id = member.user_principal_name
            elif getattr(member, "email", None):
                user_id = member.email
            else:
                user_id = teams_user_id
        except Exception as member_err:
            logger.warning(f"TeamsInfo.get_member failed: {member_err}")
            user_id = teams_user_id or "anonymous"
    except Exception as e:
        logger.warning(f"get_user_identity error: {e}")
    return user_id


async def send_formatted_response(turn_context: TurnContext, answer_text: str):
    """Split long replies, detect Source toggles, and send as Adaptive Card if needed."""
    if not answer_text.strip():
        await turn_context.send_activity(
            Activity(type="message", text="I couldn't generate a response.")
        )
        return

    # Teams message size limit ~28 KB, so chunk at 15 000 chars
    if len(answer_text) > 25000:
        chunks = [answer_text[i : i + 15000] for i in range(0, len(answer_text), 15000)]
        for idx, chunk in enumerate(chunks):
            prefix = f"(Part {idx+1}/{len(chunks)}) " if len(chunks) > 1 else ""
            await turn_context.send_activity(
                Activity(type="message", text=prefix + chunk)
            )
        return

    # Look for a “Source:” line plus optional details marker
    source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)

    if match:
        main = match.group(1).strip()
        src  = match.group(2).strip()
        det  = match.group(3).strip() if match.group(3) else ""

        body = [{"type": "TextBlock", "text": main, "wrap": True}]
        # source line always visible
        body.append({
            "type": "TextBlock", "text": src, "wrap": True,
            "id": "sourceLineBlock", "isVisible": True
        })
        if det:
            body.append({
                "type": "TextBlock", "text": det, "wrap": True,
                "id": "sourceBlock", "isVisible": False
            })

        actions = []
        if det:
            actions.append({
                "type": "Action.ToggleVisibility",
                "title": "Show Source Details",
                "targetElements": ["sourceBlock"]
            })

        card = {
            "type": "AdaptiveCard",
            "body": body,
            "actions": actions,
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
        }
        await turn_context.send_activity(
            Activity(
                type="message",
                attachments=[{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
            )
        )
    else:
        await turn_context.send_activity(
            Activity(type="message", text=answer_text)
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
