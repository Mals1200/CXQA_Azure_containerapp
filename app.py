# version 11b 
# ((Notebook-style answer formatting in Teams))

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
    """Clean up conversation states older than 24 hours"""
    with state_lock:
        current_time = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state['last_activity'] and (current_time - state['last_activity']) > 86400:  # 24 hours
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

def _render_content(blocks):
    """Notebook-style pretty text renderer for Teams output."""
    out = []
    skip_mode = False
    for blk in blocks:
        btype = blk.get("type", "")
        txt   = blk.get("text", "")
        # Skip "Referenced"/"Calculated using" sections (optional)
        if btype == "paragraph" and txt.lower().startswith(("calculated using", "referenced")):
            skip_mode = True
            continue
        if skip_mode and btype in ("paragraph", "bullet_list", "numbered_list"):
            if btype == "heading":
                skip_mode = False
            else:
                continue
        if skip_mode:
            continue
        if btype == "heading":
            out.append(txt.strip())
            out.append("")
        elif btype == "paragraph":
            out.append(txt.strip())
            out.append("")
        elif btype == "bullet_list":
            out.extend(f"• {item}" for item in blk.get("items", []))
            out.append("")
        elif btype == "numbered_list":
            out.extend(f"{i}. {item}" for i, item in enumerate(blk.get("items", []), 1))
            out.append("")
        else:
            out.append(str(blk))
            out.append("")
    return "\n".join(out).strip()

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state['last_activity'] = asyncio.get_event_loop().time()
    
    # Clean up old states periodically
    if len(conversation_states) > 100:
        cleanup_old_states()

    # Set the conversation state for this request
    import ask_func
    ask_func.chat_history = state['history']
    ask_func.tool_cache = state['cache']

    user_message = turn_context.activity.text or ""

    # --------------------------------------------------------------------
    # Use 'TeamsInfo.get_member' to get userPrincipalName or email
    # --------------------------------------------------------------------
    user_id = "anonymous"  # fallback
    try:
        teams_user_id = turn_context.activity.from_property.id
        teams_member = await TeamsInfo.get_member(turn_context, teams_user_id)
        if teams_member and teams_member.user_principal_name:
            user_id = teams_member.user_principal_name
        elif teams_member and teams_member.email:
            user_id = teams_member.email
        else:
            user_id = teams_user_id  # fallback if we can't get an email
    except Exception as e:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Show "thinking" indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # ----- Pretty notebook-style rendering logic -----
        try:
            cleaned = answer_text.strip()
            if cleaned.startswith('```json'):
                cleaned = cleaned[7:].strip()
            if cleaned.startswith('```'):
                cleaned = cleaned[3:].strip()
            if cleaned.endswith('```'):
                cleaned = cleaned[:-3].strip()
            response_json = json.loads(cleaned)
        except Exception as e:
            response_json = None

        if response_json and "content" in response_json:
            pretty_text = _render_content(response_json["content"])
            # Optionally add files/source info:
            if "source" in response_json:
                pretty_text += f"\n\nSource: {response_json['source']}"
            if "source_details" in response_json:
                det = response_json["source_details"]
                file_tables = []
                if det.get("file_names"):
                    file_tables.extend(det["file_names"])
                if det.get("table_names"):
                    file_tables.extend(det["table_names"])
                if file_tables:
                    pretty_text += "\nFiles used: " + ", ".join(file_tables)
            await turn_context.send_activity(Activity(type="message", text=pretty_text))
        else:
            # fallback: show plain answer as is
            await turn_context.send_activity(Activity(type="message", text=answer_text))

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
