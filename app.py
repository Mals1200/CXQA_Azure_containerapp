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

        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        match = re.search(source_pattern, answer_text, flags=re.DOTALL)

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
                await turn_context.send_activity(Activity(type="message", text=answer_text))
                return

            # --- Robust block limit and truncation for Teams ---
            content_items = response_json["content"]
            source = response_json["source"]
            source_details = response_json.get("source_details", {})
            file_names = source_details.get("file_names", []) or []
            table_names = source_details.get("table_names", []) or []

            body_blocks = []
            MAX_BLOCKS = 20
            MAX_LIST_ITEMS = 6
            MAX_TEXT_LENGTH = 700

            def split_long_text(text):
                parts = []
                while len(text) > MAX_TEXT_LENGTH:
                    idx = text.rfind('.', 0, MAX_TEXT_LENGTH)
                    if idx < 0:
                        idx = MAX_TEXT_LENGTH
                    parts.append(text[:idx].strip())
                    text = text[idx:].strip()
                if text:
                    parts.append(text)
                return parts

            block_count = 0
            for item in content_items:
                if block_count >= MAX_BLOCKS - 2:  # Reserve space for source and button
                    break
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
                    block_count += 1
                elif item_type == "paragraph":
                    for t in split_long_text(item.get("text", "")):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": t,
                            "wrap": True,
                            "spacing": "Small"
                        })
                        block_count += 1
                        if block_count >= MAX_BLOCKS - 2:
                            break
                elif item_type == "bullet_list":
                    items = item.get("items", [])[:MAX_LIST_ITEMS]
                    for list_item in items:
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"• {list_item}",
                            "wrap": True,
                            "spacing": "Small"
                        })
                        block_count += 1
                        if block_count >= MAX_BLOCKS - 2:
                            break
                elif item_type == "numbered_list":
                    items = item.get("items", [])[:MAX_LIST_ITEMS]
                    for i, list_item in enumerate(items, 1):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"{i}. {list_item}",
                            "wrap": True,
                            "spacing": "Small"
                        })
                        block_count += 1
                        if block_count >= MAX_BLOCKS - 2:
                            break
                elif item_type == "code_block":
                    code_txt = item.get('code', '')
                    for t in split_long_text(code_txt):
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"```\n{t}\n```",
                            "wrap": True,
                            "fontType": "Monospace",
                            "spacing": "Medium"
                        })
                        block_count += 1
                        if block_count >= MAX_BLOCKS - 2:
                            break

            if len(content_items) > MAX_BLOCKS - 2 or block_count >= MAX_BLOCKS - 2:
                body_blocks.append({
                    "type": "TextBlock",
                    "text": "**Output truncated due to Teams display limits. Ask for more details if needed.**",
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Attention",
                    "spacing": "Medium"
                })

            # --- Source section ---
            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,
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
            body_blocks.append({
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "id": "showSourceBtn",
                        "items": [
                            {
                                "type": "ActionSet",
                                "actions": [
                                    {
                                        "type": "Action.ToggleVisibility",
                                        "title": "Show Source",
                                        "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "type": "Column",
                        "id": "hideSourceBtn",
                        "isVisible": False,
                        "items": [
                            {
                                "type": "ActionSet",
                                "actions": [
                                    {
                                        "type": "Action.ToggleVisibility",
                                        "title": "Hide Source",
                                        "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            })

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

        except Exception as e:
            # fallback: regular markdown message with truncation if very long
            if len(answer_text) > 4000:
                answer_text = answer_text[:3950] + "\n\n**Output truncated due to Teams message size limit.**"
            await turn_context.send_activity(Activity(type="message", text=answer_text))
            return

        # If not valid JSON, fallback to markdown as above
        if match:
            main_answer = match.group(1).strip()
            source_line = match.group(2).strip()
            appended_details = match.group(3) if match.group(3) else ""
        else:
            main_answer = answer_text
            source_line = ""
            appended_details = ""

        if source_line:
            blocks = [{
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            }]
            if source_line or appended_details:
                source_container = {
                    "type": "Container",
                    "id": "sourceContainer",
                    "isVisible": False,
                    "style": "emphasis",
                    "bleed": True,
                    "maxHeight": "500px",
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
                    ]
                }
                if appended_details:
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": appended_details.strip(),
                        "wrap": True,
                        "spacing": "Small"
                    })
                blocks.append(source_container)
                blocks.append({
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "id": "showSourceBtn",
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Show Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        },
                        {
                            "type": "Column",
                            "id": "hideSourceBtn",
                            "isVisible": False,
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Hide Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                })
            adaptive_card = {
                "type": "AdaptiveCard",
                "body": blocks,
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
        else:
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
