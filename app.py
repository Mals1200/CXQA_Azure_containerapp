# version14

import os
import re
import json
import asyncio
from threading import Lock
from flask import Flask, request, jsonify, Response

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity

from ask_func import Ask_Question, chat_history  # noqa: F401

# ───── GLOBAL CONFIG ────────────────────────────────────────────────
MICROSOFT_APP_ID       = os.getenv("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "")
MAX_TEAMS_CARD_BYTES   = 28 * 1024  # 28 KB Teams limit
# ────────────────────────────────────────────────────────────────────

app = Flask(__name__)

adapter_settings = BotFrameworkAdapterSettings(
    MICROSOFT_APP_ID,
    MICROSOFT_APP_PASSWORD
)
adapter = BotFrameworkAdapter(adapter_settings)

# ───── IN-MEMORY STATE ──────────────────────────────────────────────
conversation_states = {}
state_lock = Lock()

def get_conversation_state(conversation_id: str):
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conversation_id]

def cleanup_old_states(max_age_seconds: int = 86_400):
    now = asyncio.get_event_loop().time()
    with state_lock:
        for cid, state in list(conversation_states.items()):
            if state["last_activity"] and (now - state["last_activity"]) > max_age_seconds:
                del conversation_states[cid]

# ───── ROUTES ───────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    activity = Activity().deserialize(request.json)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)

# ───── MAIN BOT LOGIC ───────────────────────────────────────────────
async def _bot_logic(turn_context: TurnContext):
    conv_id = turn_context.activity.conversation.id
    state = get_conversation_state(conv_id)
    state["last_activity"] = asyncio.get_event_loop().time()
    if len(conversation_states) > 100:
        cleanup_old_states()

    import ask_func
    ask_func.chat_history = state["history"]
    ask_func.tool_cache   = state["cache"]

    user_message = turn_context.activity.text or ""
    if not user_message.strip():
        return

    # Identify user
    try:
        teams_user_id = turn_context.activity.from_property.id
        member = await TeamsInfo.get_member(turn_context, teams_user_id)
        user_id = (
            member.user_principal_name
            or member.email
            or teams_user_id
        )
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    await turn_context.send_activity(Activity(type="typing"))

    try:
        # 1. Get full output from Ask_Question
        raw_chunks = list(Ask_Question(user_message, user_id=user_id))
        answer_text = "".join(raw_chunks).strip()

        # 2. If JSON structured response, extract content blocks
        try:
            parsed = json.loads(answer_text)
            if isinstance(parsed, dict) and "content" in parsed:
                content_blocks = parsed.get("content", [])
                if isinstance(content_blocks, list):
                    flattened = "\n\n".join(
                        block.get("text", "") for block in content_blocks if isinstance(block, dict)
                    )
                    source_line = parsed.get("source", "").strip()
                    if source_line:
                        flattened += f"\n\nSource: {source_line}"
                    answer_text = flattened.strip()
        except Exception:
            pass  # not JSON

        # 3. Hide broken output
        def is_broken(text):
            text = text.lower()
            return any(err in text for err in [
                "traceback", "undefined variable", "execution error",
                "failing code", "llm error", "keyerror", "attributeerror"
            ])

        if not answer_text or is_broken(answer_text):
            answer_text = "No information available.\n\nSource: Unknown"

        # 4. Send directly to Teams
        await turn_context.send_activity(answer_text)

        # 5. Save conversation state
        state["history"] = ask_func.chat_history
        state["cache"]   = ask_func.tool_cache

    except Exception as exc:
        print(f"Error during bot logic: {exc}")
        await turn_context.send_activity("Sorry, something went wrong.")

# ───── MAIN ENTRY ───────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
