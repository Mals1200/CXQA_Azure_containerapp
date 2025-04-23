# Version 6  (scrollable-source enabled)
# made source content different color(Blue) and segmented
# Button from "Show Source" to "Source"

# ─────────────────── version 8 – true fixed-height scrolling ───────────────────────────
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
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history  # noqa: F401 (globals touched)

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

conversation_states: dict[str, dict] = {}
state_lock = Lock()


def get_conversation_state(conv_id: str) -> dict:
    with state_lock:
        if conv_id not in conversation_states:
            conversation_states[conv_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conv_id]


def cleanup_old_states() -> None:
    with state_lock:
        now = asyncio.get_event_loop().time()
        for cid, state in list(conversation_states.items()):
            if state["last_activity"] and (now - state["last_activity"]) > 86_400:
                del conversation_states[cid]


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200


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


# ───────────────────────────────── BOT LOGIC ──────────────────────────────────────────
async def _bot_logic(turn_context: TurnContext):
    conv_id = turn_context.activity.conversation.id
    state = get_conversation_state(conv_id)
    state["last_activity"] = asyncio.get_event_loop().time()

    if len(conversation_states) > 100:
        cleanup_old_states()

    import ask_func

    ask_func.chat_history = state["history"]
    ask_func.tool_cache = state["cache"]

    user_msg = turn_context.activity.text or ""

    # identify Teams user
    try:
        member = await TeamsInfo.get_member(turn_context, turn_context.activity.from_property.id)
        user_id = (
            member.user_principal_name or member.email or member.id or "anonymous"
        )
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    await turn_context.send_activity(Activity(type="typing"))

    try:
        answer_text = "".join(Ask_Question(user_msg, user_id=user_id))

        state["history"] = ask_func.chat_history
        state["cache"] = ask_func.tool_cache

        import re

        m = re.search(r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$", answer_text, re.S)
        main_answer = (m.group(1) if m else answer_text).strip()
        src_header = (m.group(2).strip() if m else "")
        src_details = (m.group(3).strip() if m and m.group(3) else "")

        # ───────────── Adaptive Card construction ─────────────
        if src_header:
            body_blocks = [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True,
                    "size": "Medium",
                }
            ]

            # (1) bold source header – its own container
            header_container = {
                "type": "Container",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": src_header,
                        "wrap": True,
                        "weight": "Bolder",
                        "color": "Accent",
                    }
                ],
            }

            # (2) long details – fixed-height scrollable container
            scroll_container = {
                "type": "Container",
                "isScrollable": True,
                "height": "300px",      # ← fixed visible height
                "style": "default",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": src_details or "*No additional details provided.*",
                        "wrap": True,
                        "size": "Small",
                    }
                ],
            }

            # collapsible wrapper
            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,
                "height": "stretch",
                "items": [header_container, scroll_container],
            }

            body_blocks.extend(
                [
                    source_container,
                    {
                        "type": "ActionSet",
                        "actions": [
                            {
                                "type": "Action.ToggleVisibility",
                                "title": "Source",
                                "targetElements": ["sourceContainer"],
                            }
                        ],
                    },
                ]
            )

            card = {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.5",
                "body": body_blocks,
            }

            await turn_context.send_activity(
                Activity(
                    type="message",
                    attachments=[
                        {
                            "contentType": "application/vnd.microsoft.card.adaptive",
                            "content": card,
                        }
                    ],
                )
            )
        else:
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as exc:
        print("Error in bot logic:", exc)
        await turn_context.send_activity(
            Activity(
                type="message",
                text=f"An error occurred while processing your request: {exc}",
            )
        )


# ───────────────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)

