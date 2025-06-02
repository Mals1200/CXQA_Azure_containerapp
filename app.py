# version 12b with RENDER_MODE switch ("markdown" or "adaptivecard")
# Robust and bulletproof: Always shows references/source, never crashes, 
# works for both JSON and markdown from ask_func.py

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

# ======== TOP-LEVEL SWITCH ========
RENDER_MODE = "markdown"  # "markdown" or "adaptivecard"
# ==================================

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

MAX_TEAMS_CARD_BYTES = 28 * 1024  # 28KB

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

def extract_references_from_json_or_markdown(answer_text):
    """
    Extracts main answer, ref_names, calc_names, source from answer_text.
    Handles both JSON structure (with content/source) and markdown fallback.
    Returns: (main_answer, ref_names, calc_names, source)
    """
    # Try JSON first
    cleaned = answer_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    try:
        response_json = json.loads(cleaned)
        if isinstance(response_json, dict) and "content" in response_json:
            content_items = response_json["content"]
            main_answer_lines = []
            ref_names = []
            calc_names = []
            source = ""
            for item in content_items:
                if isinstance(item, dict):
                    t = item.get("type", "")
                    txt = item.get("text", "")
                    if t == "heading" or t == "paragraph" or t == "numbered_list" or t == "bullet_list":
                        if txt.startswith("Referenced:"):
                            for line in txt.splitlines()[1:]:
                                if line.strip().startswith("-"):
                                    ref_names.append(line.strip()[1:].strip())
                        elif txt.startswith("Calculated using:"):
                            for line in txt.splitlines()[1:]:
                                if line.strip().startswith("-"):
                                    calc_names.append(line.strip()[1:].strip())
                        elif txt.lower().startswith("source:"):
                            source = txt.split(":",1)[-1].strip()
                        else:
                            main_answer_lines.append(txt)
            # fallback: also check top-level "source"
            if not source:
                source = response_json.get("source", "")
            main_answer = "\n".join(main_answer_lines).strip()
            return main_answer, ref_names, calc_names, source
    except Exception:
        pass

    # Now fallback to markdown
    markdown = answer_text.strip()
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

    def extract_names(lines):
        names = []
        for l in lines[1:]:  # skip the first line
            l = l.strip()
            if l.startswith("-"):
                name = l[1:].strip()
                if name:
                    names.append(name)
        return names

    ref_names = extract_names(ref_lines)
    calc_names = extract_names(calc_lines)
    source = source_line.replace("**", "").replace("*", "").replace("Source:", "").strip()
    main_answer = "\n".join(main_answer_lines).strip()
    return main_answer, ref_names, calc_names, source

def extract_source_info(user_message, ask_func):
    tool_cache = getattr(ask_func, 'tool_cache', {})
    cache_key = user_message.strip().lower()
    index_dict = {}
    python_dict = {}
    if cache_key in tool_cache:
        index_dict, python_dict, _ = tool_cache[cache_key]
    file_names = index_dict.get("file_names", []) or []
    table_names = python_dict.get("table_names", []) or []
    # Determine the most likely source label:
    source = ""
    if index_dict.get("top_k", "").strip().lower() not in ["", "no information"] \
       and python_dict.get("result", "").strip().lower() not in ["", "no information"]:
        source = "Index & Python"
    elif index_dict.get("top_k", "").strip().lower() not in ["", "no information"]:
        source = "Index"
    elif python_dict.get("result", "").strip().lower() not in ["", "no information"]:
        source = "Python"
    else:
        source = "AI Generated"
    return file_names, table_names, source

def clean_main_answer(answer_text):
    """
    1. If answer_text is JSON with a "content" array, flatten it to plain text.
    2. Otherwise (or afterward), strip out any line that begins with "Source:" (case-insensitive).
    """
    cleaned = answer_text.strip()

    # ——— Step 1: Handle JSON-with-content case ———
    if cleaned.startswith("{") and '"content"' in cleaned:
        try:
            response_json = json.loads(cleaned)
            if isinstance(response_json, dict) and "content" in response_json:
                md_lines = []
                for block in response_json["content"]:
                    # Each block is a dict with keys like "type" and "text"
                    if isinstance(block, dict):
                        text_val = block.get("text", "").strip()
                        if text_val:
                            md_lines.append(text_val)
                # Join all extracted text blocks with blank lines
                return "\n\n".join(md_lines).strip()
        except Exception:
            # If JSON parsing fails, fall back to plain-text stripping below
            pass

    # ——— Step 2: Remove any "Source:" lines from plain text ———
    lines = answer_text.strip().split('\n')
    # Keep only lines that do NOT start with "Source:" (ignoring leading whitespace and case)
    filtered = [
        l for l in lines
        if not re.match(r'^\s*Source:\s*', l, flags=re.IGNORECASE)
    ]
    return "\n".join(filtered).strip()

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

        file_names, table_names, source = extract_source_info(user_message, ask_func)
        main_answer = clean_main_answer(answer_text)

        if RENDER_MODE == "markdown":
            markdown = main_answer
            sections = []
            if source in ("Index", "Index & Python") and file_names:
                sections.append("**Referenced:**\n" + "\n".join(f"- {f}" for f in file_names))
            if source in ("Python", "Index & Python") and table_names:
                sections.append("**Calculated using:**\n" + "\n".join(f"- {t}" for t in table_names))
            sections.append(f"**Source:** {source}")
            if sections:
                markdown += "\n\n" + "\n\n".join(sections)
            await turn_context.send_activity(Activity(type="message", text=markdown))
            return

        # --- AdaptiveCard mode (everything in one card with toggle) ---
        def markdown_table_to_adaptive(lines):
            table_lines = [l for l in lines if l.strip().startswith('|') and l.strip().endswith('|')]
            if len(table_lines) < 2:
                return None, None, []
            header = [h.strip() for h in table_lines[0].strip('|').split('|')]
            rows = []
            for l in table_lines[2:]:
                row = [c.strip() for c in l.strip('|').split('|')]
                if len(row) == len(header):
                    rows.append(row)
            return header, rows, table_lines

        main_answer_lines = main_answer.split("\n")
        table_header, table_rows, table_lines = markdown_table_to_adaptive(main_answer_lines)
        body_blocks = []
        import re
        export_link_match = re.search(r'https?://[^\s\)]+', main_answer)
        is_export = main_answer.strip().lower().startswith("here is your generated")
        export_url = export_link_match.group(0) if export_link_match else None
        if export_url and not is_export:
            # Insert a "Download File" button at the TOP of the card body for non-export
            main_answer = main_answer.replace(export_url, '').strip()
            body_blocks.insert(0, {
                "type": "ActionSet",
                "actions": [
                    {
                        "type": "Action.OpenUrl",
                        "title": "Download File",
                        "url": export_url
                    }
                ]
            })

        if table_header and table_rows:
            pre_table = main_answer.split('|')[0].strip()
            if pre_table:
                body_blocks.append({
                    "type": "TextBlock",
                    "text": pre_table,
                    "wrap": True,
                    "spacing": "Medium",
                    "fontType": "Default",
                    "size": "Default"
                })
            # Render header
            body_blocks.append({
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [{
                            "type": "TextBlock",
                            "text": h,
                            "weight": "Bolder",
                            "wrap": True,
                            "spacing": "Small"
                        }]
                    } for h in table_header
                ]
            })
            # Render rows
            for row in table_rows:
                body_blocks.append({
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [{
                                "type": "TextBlock",
                                "text": c,
                                "wrap": True,
                                "spacing": "Small"
                            }]
                        } for c in row
                    ]
                })
            # Add any text after the table (e.g., notes)
            if table_lines:
                after_table = main_answer.split(table_lines[-1])[-1].strip()
                if after_table:
                    body_blocks.append({
                        "type": "TextBlock",
                        "text": after_table,
                        "wrap": True,
                        "spacing": "Small"
                    })
        else:
            if main_answer:
                body_blocks.append({
                    "type": "TextBlock",
                    "text": main_answer,
                    "wrap": True,
                    "spacing": "Medium",
                    "fontType": "Default",
                    "size": "Default"
                })

        if is_export and export_url:
            # For export responses, put the Download button at the bottom and omit source/show source
            body_blocks.append({
                "type": "ActionSet",
                "actions": [
                    {
                        "type": "Action.OpenUrl",
                        "title": "Download File",
                        "url": export_url
                    }
                ]
            })
            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }
        else:
            # --- Build the source container (only relevant sections, always inside the button) ---
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
            if source in ("Index", "Index & Python"):
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": "Referenced:",
                    "weight": "Bolder",
                    "spacing": "Small",
                    "wrap": True
                })
                if file_names:
                    for fname in file_names:
                        url = "https://dgda.sharepoint.com/sites/CXQAData/SitePages/CollabHome.aspx?sw=auth"
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": f"[{fname}]({url})",
                            "wrap": True,
                            "color": "Accent"
                        })
                else:
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": "(None)",
                        "wrap": True
                    })
            if source in ("Python", "Index & Python"):
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": "Calculated using:",
                    "weight": "Bolder",
                    "spacing": "Small",
                    "wrap": True
                })
                if table_names:
                    for tname in table_names:
                        url = "https://dgda.sharepoint.com/sites/CXQAData/SitePages/CollabHome.aspx?sw=auth"
                        source_container["items"].append({
                            "type": "TextBlock",
                            "text": f"[{tname}]({url})",
                            "wrap": True,
                            "color": "Accent"
                        })
                else:
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": "(None)",
                        "wrap": True
                    })
            source_container["items"].append({
                "type": "TextBlock",
                "text": f"Source: {source or '(Unknown)'}",
                "weight": "Bolder",
                "color": "Accent",
                "spacing": "Medium",
            })

            # --- Show/Hide Source toggle ---
            show_hide_buttons = {
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "id": "showSourceBtn",
                        "isVisible": True,
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
            }

            # --- Assemble the Adaptive Card body ---
            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks + [source_container, show_hide_buttons],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }
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

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
