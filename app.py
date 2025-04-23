# Version 6  (scrollable-source enabled)
# made source content different color (Blue) and segmented
# Button from "Show Source" to "Source"

import os
import asyncio
from threading import Lock

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity
# *** Important: import TeamsInfo ***
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history  # noqa: F401  (import used dynamically)

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# ────────────────────────── Conversation-state cache ──────────────────────────
conversation_states: dict[str, dict] = {}
state_lock = Lock()


def get_conversation_state(conversation_id: str) -> dict:
    """Return the per-conversation state dict (creates on first access)."""
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conversation_id]


def cleanup_old_states() -> None:
    """Remove states that haven’t been touched for 24 h (to save memory)."""
    with state_lock:
        now = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state["last_activity"] and (now - state["last_activity"]) > 86_400:  # 24 h
                del conversation_states[conv_id]


# ─────────────────────────────────── Flask routes ──────────────────────────────
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


# ───────────────────────────── Bot logic callback ──────────────────────────────
async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state["last_activity"] = asyncio.get_event_loop().time()

    # purge old conversation caches if we’re holding too many
    if len(conversation_states) > 100:
        cleanup_old_states()

    # expose history & cache to ask_func
    import ask_func  # imported here to avoid circular-import issues
    ask_func.chat_history = state["history"]
    ask_func.tool_cache = state["cache"]

    user_message = turn_context.activity.text or ""

    # ─────────── Resolve Teams user email / UPN (graceful fallbacks) ────────────
    try:
        teams_user_id = turn_context.activity.from_property.id
        teams_member = await TeamsInfo.get_member(turn_context, teams_user_id)

        if teams_member and teams_member.user_principal_name:
            user_id = teams_member.user_principal_name
        elif teams_member and teams_member.email:
            user_id = teams_member.email
        else:
            user_id = teams_user_id
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # “typing…” indicator
    await turn_context.send_activity(Activity(type="typing"))

    # ────────────────────────── Ask the LLM-backed assistant ───────────────────
    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # preserve updated history/cache for next turn
        state["history"] = ask_func.chat_history
        state["cache"] = ask_func.tool_cache

        # ───────────── Parse answer → main text + (Source …) & details ──────────
        import re

        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        m = re.search(source_pattern, answer_text, flags=re.DOTALL)

        if m:
            main_answer = m.group(1).strip()
            source_line = m.group(2).strip()
            appended_details = (m.group(3) or "").strip()
        else:
            main_answer, source_line, appended_details = answer_text, "", ""

        # ────────────────────── Build & send Adaptive Card ──────────────────────
        if source_line:
            body_blocks = [
                {  # main answer block
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True,
                    "size": "Medium",
                }
            ]

            # Collapsible parent container
            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,  # hidden until toggled
                "items": [
                    # 1️⃣ Source header
                    {
                        "type": "Container",
                        "style": "emphasis",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": source_line,
                                "wrap": True,
                                "weight": "Bolder",
                                "color": "Accent",
                            }
                        ],
                    },
                    # 2️⃣ Scrollable details with max height
                    {
                        "type": "Container",
                        "style": "default",
                        "maxHeight": "250px",  # <── Teams adds scrollbar beyond 250 px
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": appended_details or "(no extra details)",
                                "wrap": True,
                                "size": "Small",
                            }
                        ],
                    },
                ],
            }

            body_blocks.append(source_container)

            # Toggle button
            body_blocks.append(
                {
                    "type": "ActionSet",
                    "actions": [
                        {
                            "type": "Action.ToggleVisibility",
                            "title": "Source",
                            "targetElements": ["sourceContainer"],
                        }
                    ],
                }
            )

            adaptive_card = {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5",
                "body": body_blocks,
            }

            await turn_context.send_activity(
                Activity(
                    type="message",
                    attachments=[
                        {
                            "contentType": "application/vnd.microsoft.card.adaptive",
                            "content": adaptive_card,
                        }
                    ],
                )
            )
        else:
            # no “Source:” in answer → plain text reply
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as e:
        await turn_context.send_activity(
            Activity(
                type="message",
                text=f"An error occurred while processing your request: {e}",
            )
        )
        print(f"[bot-logic] error: {e}")


# ─────────────────────────────────── Entrypoint ────────────────────────────────
if __name__ == "__main__":
    # listens on :80 inside the container; map externally as needed
    app.run(host="0.0.0.0", port=80)
