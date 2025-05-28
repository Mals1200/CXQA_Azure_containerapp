import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse
import traceback

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

conversation_states = {}
state_lock = Lock()

def get_conversation_state(conversation_id):
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                'history': [],
                'cache': {},
                'last_activity': None
            }
        return conversation_states[conversation_id]

def cleanup_old_states():
    with state_lock:
        current_time = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state['last_activity'] and (current_time - state['last_activity']) > 86400:
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
    import traceback
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state['last_activity'] = asyncio.get_event_loop().time()
    if len(conversation_states) > 100:
        cleanup_old_states()

    import ask_func
    ask_func.chat_history = state['history']
    ask_func.tool_cache = state['cache']

    user_message = turn_context.activity.text or ""
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

        # --- Parse JSON if possible ---
        cleaned = answer_text.strip()
        if cleaned.startswith('```json'):
            cleaned = cleaned[7:].strip()
        if cleaned.startswith('```'):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3].strip()

        try:
            response_json = json.loads(cleaned)
            content_items = response_json["content"] if isinstance(response_json, dict) and "content" in response_json else []
            num_blocks = len(content_items)
            num_list_items = 0
            MAX_LIST_ITEMS = 5
            truncated = False

            # Truncate all lists in the answer
            for item in content_items:
                if item.get("type") in ("bullet_list", "numbered_list"):
                    items = item.get("items", [])
                    if len(items) > MAX_LIST_ITEMS:
                        item["items"] = items[:MAX_LIST_ITEMS] + [f"...and {len(items) - MAX_LIST_ITEMS} more steps. See SOP for full details."]
                        truncated = True
                    num_list_items += len(item.get("items", []))

            # Build the Adaptive Card
            body_blocks = []
            for item in content_items:
                item_type = item.get("type", "")
                if item_type == "heading":
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": item.get("text", ""),
                        "wrap": True,
                        "weight": "Bolder",
                        "size": "Large",
                        "spacing": "Medium"
                    })
                elif item_type == "paragraph":
                    text = item.get("text", "")
                    if not (text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:")):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": text,
                            "wrap": True,
                            "spacing": "Small"
                        })
                elif item_type == "bullet_list":
                    for list_item in item.get("items", []):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"• {list_item}",
                            "wrap": True,
                            "spacing": "Small"
                        })
                elif item_type == "numbered_list":
                    for i, list_item in enumerate(item.get("items", []), 1):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"{i}. {list_item}",
                            "wrap": True,
                            "spacing": "Small"
                        })
                elif item_type == "code_block":
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": f"```\n{item.get('code', '')}\n```",
                        "wrap": True,
                        "fontType": "Monospace",
                        "spacing": "Medium"
                    })
            # Show/hide source block/buttons (add your existing code here)
            # ...

            # Add a debug footer if truncated
            if truncated:
                body_blocks.append({
                    "type": "TextBlock",
                    "text": f"DEBUG: List truncated to {MAX_LIST_ITEMS} items per list to fit Teams limits.",
                    "wrap": True,
                    "size": "Small",
                    "weight": "Lighter",
                    "color": "Accent"
                })

            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }

            # Send debug info as a separate message (optional)
            debug_msg = f"DEBUG: {num_blocks} blocks, {num_list_items} total list items. Truncated: {truncated}"
            await turn_context.send_activity(Activity(type="message", text=debug_msg))

            # Send the Adaptive Card
            try:
                message = Activity(
                    type="message",
                    attachments=[{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": adaptive_card
                    }]
                )
                await turn_context.send_activity(message)
            except Exception as card_error:
                # Fallback: send plain text if card fails
                await turn_context.send_activity(Activity(type="message", text=f"DEBUG: Adaptive Card failed: {card_error}\nShowing plain text answer."))
                await turn_context.send_activity(Activity(type="message", text=answer_text[:2000]))

        except Exception as json_exc:
            # Fallback: send plain text if JSON fails
            await turn_context.send_activity(Activity(type="message", text=f"DEBUG: JSON/adaptive card parse failed: {json_exc}\nShowing plain text answer."))
            await turn_context.send_activity(Activity(type="message", text=answer_text[:2000]))

    except Exception as e:
        error_message = f"An error occurred: {str(e)}\n{traceback.format_exc()}"
        await turn_context.send_activity(Activity(type="message", text=error_message))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
