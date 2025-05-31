# version 11c with Adaptive Card Size Limiting for Teams (28KB max)
# ((Hyperlink file names))
# Made it display the files sources for the compounded questions:
#     Referenced: <Files>                <-------Hyperlink to sharepoint
#     Calculated using: <Tables>         <-------Hyperlink to sharepoint
# Adaptive Card output is automatically truncated if it exceeds 28KB.
# If truncated, user gets a simple error card instead of no answer.
# (still the url is fixed to one file. NEEDS WORK!)

import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse
import re as _re  # for robust source extraction

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

# Teams Adaptive Card byte limit (official: 28KB)
MAX_TEAMS_CARD_BYTES = 28 * 1024  # 28KB

# Thread-safe conversation state management
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

def adaptive_card_size_ok(card_dict):
    # Returns True if JSON serialized card is <= 28KB
    card_json = json.dumps(card_dict, ensure_ascii=False)
    return len(card_json.encode("utf-8")) <= MAX_TEAMS_CARD_BYTES

def make_fallback_card():
    return {
        "type": "AdaptiveCard",
        "body": [
            {
                "type": "TextBlock",
                "text": (
                    "Sorry, the answer is too large to display in Microsoft Teams. "
                    "Please refine your question or check the official source document."
                ),
                "wrap": True,
                "weight": "Bolder",
                "color": "Attention"
            }
        ],
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5"
    }

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

        try:
            cleaned_answer_text = answer_text.strip()
            if cleaned_answer_text.startswith('```json'):
                cleaned_answer_text = cleaned_answer_text[7:].strip()
            if cleaned_answer_text.startswith('```'):
                cleaned_answer_text = cleaned_answer_text[3:].strip()
            if cleaned_answer_text.endswith('```'):
                cleaned_answer_text = cleaned_answer_text[:-3].strip()
            cleaned_answer_text = cleaned_answer_text.replace('\n', '\n')
            response_json = json.loads(cleaned_answer_text)
            if isinstance(response_json, dict) and "content" in response_json and "source" in response_json:
                content_items = response_json["content"]
                source = response_json["source"]
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
                        items = item.get("items", [])
                        for list_item in items:
                            body_blocks.append({
                                "type": "TextBlock",
                                "text": f"• {list_item}",
                                "wrap": True,
                                "spacing": "Small"
                            })
                    elif item_type == "numbered_list":
                        items = item.get("items", [])
                        for i, list_item in enumerate(items, 1):
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
                for item in content_items:
                    if item.get("type", "") == "paragraph":
                        text = item.get("text", "")
                        if text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:"):
                            lines = text.split("\n")
                            if lines:
                                source_container["items"].append({
                                    "type": "TextBlock",
                                    "text": lines[0],
                                    "wrap": True,
                                    "spacing": "Small",
                                    "weight": "Bolder"
                                })
                            for line in lines[1:]:
                                if line.strip().startswith("-"):
                                    fname = line.strip()[1:].strip()
                                    if fname:
                                        sharepoint_base = "https://dgda.sharepoint.com/sites/CXQAData/SitePages/CollabHome.aspx?sw=auth"
                                        url = sharepoint_base.format(urllib.parse.quote(fname))
                                        source_container["items"].append({
                                            "type": "TextBlock",
                                            "text": f"[{fname}]({url})",
                                            "wrap": True,
                                            "spacing": "Small"
                                        })
                                else:
                                    source_container["items"].append({
                                        "type": "TextBlock",
                                        "text": line,
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
                # SIZE CHECK for Teams
                if not adaptive_card_size_ok(adaptive_card):
                    adaptive_card = make_fallback_card()
                message = Activity(
                    type="message",
                    attachments=[{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": adaptive_card
                    }]
                )
                await turn_context.send_activity(message)
                return
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # --- Markdown/Plaintext Handling (Two Replies: Markdown + Source Card) ---
        markdown = answer_text.strip()
        # Remove code fences if present
        if markdown.startswith('```markdown'):
            markdown = markdown[len('```markdown'):].strip()
        if markdown.startswith('```'):
            markdown = markdown[3:].strip()
        if markdown.endswith('```'):
            markdown = markdown[:-3].strip()

        lines = markdown.split('\n')
        main_answer_lines = []
        ref_lines = []
        calc_lines = []
        source_line = ""
        in_refs = False
        in_calcs = False
        for line in lines:
            lstr = line.strip()
            if lstr.lower().startswith("referenced:"):
                in_refs = True
                in_calcs = False
                ref_lines.append(lstr)
                continue
            elif lstr.lower().startswith("calculated using:"):
                in_refs = False
                in_calcs = True
                calc_lines.append(lstr)
                continue
            elif lstr.lower().startswith("source:"):
                source_line = lstr
                in_refs = False
                in_calcs = False
                continue
            if in_refs:
                ref_lines.append(lstr)
            elif in_calcs:
                calc_lines.append(lstr)
            else:
                main_answer_lines.append(line)

        main_answer = "\n".join(main_answer_lines).strip()

        # 1. SEND MAIN ANSWER (Markdown)
        await turn_context.send_activity(Activity(type="message", text=main_answer))

        # 2. SEND SOURCE CARD (References/Files/Source)
        def extract_names(lines):
            names = []
            for l in lines[1:]:  # skip the first line ("Referenced:" or "Calculated using:")
                l = l.strip()
                if l.startswith("-"):
                    name = l[1:].strip()
                    if name:
                        names.append(name)
            return names

        ref_names = extract_names(ref_lines)
        calc_names = extract_names(calc_lines)
        source = source_line.replace("**", "").replace("*", "").replace("Source:", "").strip()

        def make_source_card(ref_names, calc_names, source):
            card = {
                "type": "AdaptiveCard",
                "body": [],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }
            if ref_names:
                card["body"].append({
                    "type": "TextBlock", "text": "Referenced Files:", "weight": "Bolder", "spacing": "Medium"
                })
                for fname in ref_names:
                    url = "https://dgda.sharepoint.com/sites/CXQAData/SitePages/CollabHome.aspx?sw=auth"
                    card["body"].append({
                        "type": "TextBlock",
                        "text": f"[{fname}]({url})",
                        "wrap": True,
                        "color": "Accent"
                    })
            if calc_names:
                card["body"].append({
                    "type": "TextBlock", "text": "Calculated using:", "weight": "Bolder", "spacing": "Medium"
                })
                for tname in calc_names:
                    url = "https://dgda.sharepoint.com/sites/CXQAData/SitePages/CollabHome.aspx?sw=auth"
                    card["body"].append({
                        "type": "TextBlock",
                        "text": f"[{tname}]({url})",
                        "wrap": True,
                        "color": "Accent"
                    })
            if source:
                card["body"].append({
                    "type": "TextBlock",
                    "text": f"Source: {source}",
                    "weight": "Bolder",
                    "color": "Accent",
                    "spacing": "Medium",
                })
            return card

        source_card = make_source_card(ref_names, calc_names, source)
        if not adaptive_card_size_ok(source_card):
            source_card = make_fallback_card()

        card_message = Activity(
            type="message",
            attachments=[{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": source_card
            }]
        )
        await turn_context.send_activity(card_message)
        return

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
