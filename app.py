# version 11c
# Max-safe output, always delivers something, truncates if needed.

import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse

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

# === Teams/Adaptive Card Limits ===
MAX_TEAMS_MSG_LENGTH = 3900  # ~4k is safe, leave margin for formatting
MAX_BLOCKS = 40              # Adaptive Card block limit (safe)

def split_message(text, max_len=MAX_TEAMS_MSG_LENGTH):
    """Splits long strings into Teams-safe chunks."""
    parts = []
    while text:
        part = text[:max_len]
        parts.append(part)
        text = text[max_len:]
    return parts

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
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state['last_activity'] = asyncio.get_event_loop().time()

    if len(conversation_states) > 100:
        cleanup_old_states()

    # Set state for ask_func
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

    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # Hard cap on giant answers (prevents rare Teams failures)
        if len(answer_text) > 30000:
            answer_text = answer_text[:29800] + "\n\n**Output truncated due to Teams/app limits. Please ask a more specific question.**"

        # Try to parse JSON for Adaptive Card
        try:
            cleaned_answer_text = answer_text.strip()
            if cleaned_answer_text.startswith('```json'):
                cleaned_answer_text = cleaned_answer_text[7:].strip()
            if cleaned_answer_text.startswith('```'):
                cleaned_answer_text = cleaned_answer_text[3:].strip()
            if cleaned_answer_text.endswith('```'):
                cleaned_answer_text = cleaned_answer_text[:-3].strip()
            response_json = json.loads(cleaned_answer_text)
            if not (isinstance(response_json, dict) and "content" in response_json and "source" in response_json):
                raise Exception("JSON response format not recognized")
            content_items = response_json["content"]
            source = response_json["source"]
            source_details = response_json.get("source_details", {})
            file_names = source_details.get("file_names", []) or []
            table_names = source_details.get("table_names", []) or []

            # -- Adaptive Card: enforce block and char limits --
            body_blocks = []
            block_count = 0
            card_char_count = 0
            for item in content_items:
                if block_count >= MAX_BLOCKS or card_char_count >= MAX_TEAMS_MSG_LENGTH:
                    break
                item_type = item.get("type", "")
                txt = item.get("text", "")
                # Each block is ~80 chars for header/paragraph, less for bullets
                if item_type == "heading":
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": txt[:400],  # Safety: cut super-long titles
                        "wrap": True,
                        "weight": "Bolder",
                        "size": "Large",
                        "spacing": "Medium"
                    })
                elif item_type == "paragraph":
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": txt[:1200],
                        "wrap": True,
                        "spacing": "Small"
                    })
                elif item_type == "bullet_list":
                    items = item.get("items", [])
                    for list_item in items:
                        if block_count >= MAX_BLOCKS or card_char_count >= MAX_TEAMS_MSG_LENGTH:
                            break
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"• {list_item}"[:1200],
                            "wrap": True,
                            "spacing": "Small"
                        })
                        block_count += 1
                        card_char_count += len(list_item)
                elif item_type == "numbered_list":
                    items = item.get("items", [])
                    for i, list_item in enumerate(items, 1):
                        if block_count >= MAX_BLOCKS or card_char_count >= MAX_TEAMS_MSG_LENGTH:
                            break
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"{i}. {list_item}"[:1200],
                            "wrap": True,
                            "spacing": "Small"
                        })
                        block_count += 1
                        card_char_count += len(list_item)
                elif item_type == "code_block":
                    code_text = item.get("code", "")
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": f"```\n{code_text[:2000]}\n```",
                        "wrap": True,
                        "fontType": "Monospace",
                        "spacing": "Medium"
                    })
                block_count += 1
                card_char_count += len(txt)
            # Truncate with a note if needed
            if block_count >= MAX_BLOCKS or card_char_count >= MAX_TEAMS_MSG_LENGTH:
                body_blocks.append({
                    "type": "TextBlock",
                    "text": "**Output truncated to fit Teams/Adaptive Card limits.**",
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Attention"
                })

            # Source section (always visible, not toggled for reliability)
            source_container = {
                "type": "Container",
                "style": "emphasis",
                "bleed": True,
                "maxHeight": "500px",
                "isScrollable": True,
                "items": []
            }
            if file_names:
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": "Referenced:",
                    "wrap": True,
                    "spacing": "Small",
                    "weight": "Bolder"
                })
                for fname in file_names:
                    sharepoint_base = "https://dgda.sharepoint.com/:x:/r/sites/CXQAData/_layouts/15/Doc.aspx?sourcedoc=%7B9B3CA3CD-5044-45C7-8A82-0604A1675F46%7D&file={}&action=default&mobileredirect=true"
                    url = sharepoint_base.format(urllib.parse.quote(fname))
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": f"[{fname}]({url})",
                        "wrap": True,
                        "spacing": "Small"
                    })
            if table_names:
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": "Calculated using:",
                    "wrap": True,
                    "spacing": "Small",
                    "weight": "Bolder"
                })
                for tname in table_names:
                    sharepoint_base = "https://dgda.sharepoint.com/:x:/r/sites/CXQAData/_layouts/15/Doc.aspx?sourcedoc=%7B9B3CA3CD-5044-45C7-8A82-0604A1675F46%7D&file={}&action=default&mobileredirect=true"
                    url = sharepoint_base.format(urllib.parse.quote(tname))
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": f"[{tname}]({url})",
                        "wrap": True,
                        "spacing": "Small"
                    })
            source_container["items"].append({
                "type": "TextBlock",
                "text": f"Source: {source}",
                "wrap": True,
                "weight": "Bolder",
                "color": "Accent",
                "spacing": "Medium",
            })
            body_blocks.append(source_container)

            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }
            message = Activity(
                type="message",
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card
                }]
            )
            await turn_context.send_activity(message)
            return

        except Exception:
            # If JSON fails, fallback to plain text and split if needed
            parts = split_message(answer_text)
            for i, msg_part in enumerate(parts):
                footer = ""
                if i == len(parts) - 1 and len(parts) > 1:
                    footer = "\n\n**Output truncated to fit Microsoft Teams limits.**"
                await turn_context.send_activity(Activity(type="message", text=msg_part + footer))
            return

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
