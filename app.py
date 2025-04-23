# Version 7
# Fixed scrollable source


import asyncio
from threading import Lock

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity
from botbuilder.core.teams import TeamsInfo  # ← IMPORTANT!

from ask_func import Ask_Question, chat_history  # noqa: F401  (used implicitly)

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Thread-safe conversation state management
conversation_states: dict[str, dict] = {}
state_lock = Lock()


def get_conversation_state(conversation_id: str) -> dict:
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conversation_id]


def cleanup_old_states() -> None:
    """Remove conversation states that have been idle for 24 hours."""
    with state_lock:
        now = asyncio.get_event_loop().time()
        for cid, state in list(conversation_states.items()):
            if state["last_activity"] and (now - state["last_activity"]) > 86_400:  # 24 h
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
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state["last_activity"] = asyncio.get_event_loop().time()

    if len(conversation_states) > 100:  # tidy up occasionally
        cleanup_old_states()

    # Sync ask_func’s globals with this conversation’s state
    import ask_func

    ask_func.chat_history = state["history"]
    ask_func.tool_cache = state["cache"]

    user_message = turn_context.activity.text or ""

    # ── identify user (tries UPN/email, otherwise falls back) ──
    try:
        teams_member = await TeamsInfo.get_member(
            turn_context, turn_context.activity.from_property.id
        )
        user_id = (
            teams_member.user_principal_name
            or teams_member.email
            or teams_member.id
            or "anonymous"
        )
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    await turn_context.send_activity(Activity(type="typing"))  # thinking indicator

    try:
        # ------- generate the answer -------
        answer_text = "".join(Ask_Question(user_message, user_id=user_id))

        # Persist updated history/cache
        state["history"] = ask_func.chat_history
        state["cache"] = ask_func.tool_cache

        # ------- split main answer from sources -------
        import re

        m = re.search(r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$", answer_text, re.S)
        main_answer = (m.group(1) if m else answer_text).strip()
        source_line = (m.group(2).strip() if m else "")
        appended_details = (m.group(3).strip() if m and m.group(3) else "")

        # ────────────────── build adaptive card ──────────────────
        if source_line:
            body_blocks = [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True,
                    "size": "Medium",
                }
            ]

            # The scrollable payload: we embed the source(s) inside a
            # Container that has **isScrollable=true** and **height="stretch"**
            scrollable_container = {
                "type": "Container",
                "isScrollable": True,     # ← this enables scrolling
                "height": "stretch",      # ← allows the scroll-area to live
                "minHeight": "200px",     # ← visible before scrolling kicks in
                "style": "default",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": source_line,
                        "wrap": True,
                        "weight": "Bolder",
                        "color": "Accent",
                    }
                ],
            }

            if appended_details:
                scrollable_container["items"].append(
                    {
                        "type": "TextBlock",
                        "text": appended_details,
                        "wrap": True,
                        "spacing": "Small",
                        "size": "Small",
                    }
                )

            # Collapsible wrapper so the user can show/hide it
            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,
                "height": "stretch",
                "items": [scrollable_container],
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

            adaptive_card = {
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
                            "content": adaptive_card,
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
