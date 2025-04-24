# Version 7b (UW)

import os
import asyncio
from threading import Lock
import re

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(
    MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD
)
adapter = BotFrameworkAdapter(adapter_settings)

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
    with state_lock:
        current_time = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state["last_activity"] and (current_time - state["last_activity"]) > 86400:
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


def format_text_for_adaptive_card(text: str):
    """
    Convert plain-text (with \\n separators) to an array of Adaptive-Card
    elements that preserve paragraphs, lists, and basic markdown.

    Handles:
        • Numbered lists    1. 2. 3.
        • Bullet lists      - • *
        • “Colon lists”     Title: explanation
        • Regular paragraphs / headings
    """
    if not text:
        return [{"type": "TextBlock", "text": "", "wrap": True}]

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    bullet_re = re.compile(r"^\s*(?:[-•*]|\d+\.)\s+")
    colon_re = re.compile(r"^[A-Z][^:\n]{0,60}:")
    blank_re = re.compile(r"^\s*$")

    elements = []

    raw_paragraphs, buf = [], []
    for line in text.split("\n"):
        if blank_re.match(line):
            if buf:
                raw_paragraphs.append("\n".join(buf))
                buf = []
        else:
            buf.append(line.rstrip())
    if buf:
        raw_paragraphs.append("\n".join(buf))

    for para in raw_paragraphs:
        lines = para.split("\n")

        # ordered / bullet list
        if sum(1 for l in lines if bullet_re.match(l)) >= len(lines) * 0.5:
            items = [
                {
                    "type": "TextBlock",
                    "text": l.strip(),
                    "wrap": True,
                    "spacing": "none",
                }
                for l in lines
                if l.strip()
            ]
            elements.append({"type": "Container", "items": items, "spacing": "medium"})
            continue

        # colon list
        if len(lines) >= 2 and all(colon_re.match(l) for l in lines):
            items = [
                {
                    "type": "TextBlock",
                    "text": l.strip(),
                    "wrap": True,
                    "spacing": "none",
                }
                for l in lines
            ]
            elements.append({"type": "Container", "items": items, "spacing": "medium"})
            continue

        # heading
        if len(lines) == 1 and len(lines[0].split()) <= 8 and lines[0].istitle():
            elements.append(
                {
                    "type": "TextBlock",
                    "text": lines[0].strip(),
                    "wrap": True,
                    "weight": "Bolder",
                    "size": "Medium",
                    "spacing": "medium",
                }
            )
            continue

        # fallback – keep each line
        for l in lines:
            if l.strip():
                elements.append(
                    {
                        "type": "TextBlock",
                        "text": l.strip(),
                        "wrap": True,
                        "spacing": "none",
                    }
                )
        elements.append({"type": "TextBlock", "text": "", "spacing": "small"})

    if elements and elements[-1].get("text", "") == "":
        elements.pop()

    return elements


async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state["last_activity"] = asyncio.get_event_loop().time()

    if len(conversation_states) > 100:
        cleanup_old_states()

    import ask_func

    ask_func.chat_history = state["history"]
    ask_func.tool_cache = state["cache"]

    user_message = turn_context.activity.text or ""

    # identify user
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

    await turn_context.send_activity(Activity(type="typing"))

    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        state["history"] = ask_func.chat_history
        state["cache"] = ask_func.tool_cache

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
            body_blocks = format_text_for_adaptive_card(main_answer)

            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,
                "style": "emphasis",
                "bleed": True,
                "maxHeight": "200px",
                "isScrollable": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": source_line,
                        "wrap": True,
                        "weight": "Bolder",
                        "color": "Accent",
                        "spacing": "Medium",
                    }
                ],
            }

            if appended_details:
                details_blocks = format_text_for_adaptive_card(appended_details.strip())
                source_container["items"].append(
                    {"type": "Container", "items": details_blocks, "spacing": "Small"}
                )

            body_blocks.append(source_container)

            body_blocks.append(
                {
                    "type": "ActionSet",
                    "spacing": "Medium",
                    "actions": [
                        {
                            "type": "Action.ToggleVisibility",
                            "id": "showSourceBtn",
                            "title": "Show Source",
                            "targetElements": [
                                "sourceContainer",
                                "showSourceBtn",
                                "hideSourceBtn",
                            ],
                        },
                        {
                            "type": "Action.ToggleVisibility",
                            "id": "hideSourceBtn",
                            "title": "Hide Source",
                            "isVisible": False,
                            "targetElements": [
                                "sourceContainer",
                                "showSourceBtn",
                                "hideSourceBtn",
                            ],
                        },
                    ],
                }
            )

            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5",
            }

            message = Activity(
                type="message",
                attachments=[
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": adaptive_card,
                    }
                ],
            )
            await turn_context.send_activity(message)
        else:
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
