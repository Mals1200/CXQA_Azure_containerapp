# Version 6  (scrollable-source enabled)
# made source content different color(Blue) and segmented
# Button from "Show Source" to "Source"

import os
import asyncio
from threading import Lock

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity
# *** Important: import TeamsInfo ***
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Thread-safe conversation state management
conversation_states = {}
state_lock = Lock()

def get_conversation_state(conversation_id):
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conversation_id]

def cleanup_old_states():
    """Clean up conversation states older than 24 h"""
    with state_lock:
        current_time = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state["last_activity"] and (current_time - state["last_activity"]) > 86_400:
                del conversation_states[conv_id]

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

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state["last_activity"] = asyncio.get_event_loop().time()

    # Periodic purge
    if len(conversation_states) > 100:
        cleanup_old_states()

    # Hand off state to Ask_Question helpers
    import ask_func
    ask_func.chat_history = state["history"]
    ask_func.tool_cache = state["cache"]

    user_message = turn_context.activity.text or ""

    # ---------------------------------------------------------
    # Resolve Teams user → email / UPN
    # ---------------------------------------------------------
    user_id = "anonymous"
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

    # “Typing…” hint
    await turn_context.send_activity(Activity(type="typing"))

    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # ------------------- persist state --------------------
        state["history"] = ask_func.chat_history
        state["cache"]   = ask_func.tool_cache

        # ------------------- parse answer --------------------
        import re
        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        match = re.search(source_pattern, answer_text, flags=re.DOTALL)

        if match:
            main_answer     = match.group(1).strip()
            source_line     = match.group(2).strip()
            appended_details = match.group(3) or ""
        else:
            main_answer, source_line, appended_details = answer_text, "", ""

        # ---------------- build adaptive card ---------------
        if source_line:
            body_blocks = [
                {
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True,
                    "size": "Medium",
                }
            ]

            # Collapsible container
            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,
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
            }

            # Scrollable inner details (***THIS IS THE FIX***)
            if appended_details:
                scrollable_details = {
                    "type": "Container",
                    "maxHeight": "250px",   # Teams adds a scrollbar beyond this point
                    "style": "default",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": appended_details.strip(),
                            "wrap": True,
                            "size": "Small",
                        }
                    ],
                }
                source_container["items"].append(scrollable_details)

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
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5",
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

    except Exception as e:
        await turn_context.send_activity(
            Activity(
                type="message",
                text=f"An error occurred while processing your request: {e}",
            )
        )
        print(f"Error in bot logic: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
