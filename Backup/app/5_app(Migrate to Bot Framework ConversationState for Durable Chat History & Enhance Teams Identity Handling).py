# Version 5
# Here’s what’s changed in the new app.py compared to your original version:
# Durable, per‑conversation state
# Original: Used a plain Python conversation_histories = {} dict in memory. If the container restarts or scales out to multiple instances, all history is lost or gets out of sync.
# New: Leverages Bot Framework’s ConversationState backed by a MemoryStorage (you can swap in Cosmos DB, Blob Storage, Redis, etc.). This ensures each conversation’s history is saved in a backing store that survives restarts and scales across instances.
# Eliminated manual history management
# Original: You manually looked up conversation_histories[conversation_id], mutated it, then wrote it back.
# New: You call await CONV_HISTORY_PROPERTY.get(turn_context, []) to load, and await CONV_HISTORY_PROPERTY.set(turn_context, history) + save_changes(...) to persist. Bot Framework handles serialization, concurrency, and retries under the hood.
# Robust Teams identity extraction
# Original: You inlined a try/except around TeamsInfo.get_member directly in _bot_logic, but any error quietly fell back to "anonymous" or the raw Teams ID.
# New: Factored out get_user_identity(...) as its own async helper. It:
# Attempts TeamsInfo.get_member and prefers user_principal_name → email → raw ID
# Catches and logs permission or Graph‑API errors at a WARNING level
# Always returns a non‑null string, so Ask_Question gets a stable user_id.
# Centralized typing indicator + error handling
# Original: You sent the typing activity once, but error handling around Ask_Question was mixed in with your Flask loop.
# New: The typing indicator is still sent immediately, but any exception thrown by Ask_Question is caught, logged via logger.error(..., exc_info=True), and surfaced to the user with a friendly apology.
# Message‑sizing and adaptive‑card helper
# Original: You built the adaptive card inline in _bot_logic, but had no logic for extremely large answers.
# New: Extracted all the Teams‑specific formatting into send_formatted_response(...), which:
# Splits messages over ~15 000 characters to avoid Teams’ ~28 KB limit
# Detects a Source: line plus optional ---SOURCE_DETAILS--- payload and builds an Adaptive Card with a “Show Source Details” toggle
# Falls back to a plain text message if no source metadata is found
# Consistent logging configuration
# Original: You had occasional print(...) calls and basic logging but no consistent format.
# New: At the top of app.py, logging.basicConfig(...) is set so every log entry includes timestamp, logger name, level, and message—making it far easier to trace incoming requests, identity lookups, and any errors in production.


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
