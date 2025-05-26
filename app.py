# version 11
# Made it display the files sources for the compounded questions:
    # Referenced: <Files>     
    # Calculated using: <Tables>

import os
import asyncio
from threading import Lock
import re
import json

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

def _render_content(blocks):
    out = []
    skip_mode = False
    for blk in blocks:
        btype = blk.get("type", "")
        txt   = blk.get("text", "")
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

def _parse_answer(full):
    try:
        js = json.loads(full)
        answer      = _render_content(js.get("content", [])) or full
        source_type = js.get("source", "Unknown")
        det         = js.get("source_details", {})
        files       = det.get("file_names", []) + det.get("table_names", [])
        files_used  = ", ".join(files)
        return answer, source_type, files_used
    except (json.JSONDecodeError, TypeError):
        pass
    if "Source:" in full:
        ans, src_part = full.split("Source:", 1)
        ans_clean = ans.strip()
        src_lines = [l.strip() for l in src_part.splitlines() if l.strip()]
        src_type  = src_lines[0] if src_lines else "Unknown"
        files     = ", ".join(src_lines[1:]) if len(src_lines) > 1 else ""
        return ans_clean, src_type, files
    return full.strip(), "Unknown", ""

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
    
    # Clean up old states periodically
    if len(conversation_states) > 100:  # Only clean up if we have many states
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
        answer_chunks = []
        try:
            for chunk in Ask_Question(user_message, user_id=user_id):
                answer_chunks.append(chunk)
            answer_text = "".join(answer_chunks)
        except Exception as e:
            answer_text = f"Sorry, an error occurred while answering your question: {e}"

        if not answer_text.strip():
            answer_text = "Sorry, I couldn't find an answer or something went wrong."

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # --- NEW: Always parse and display a clean answer ---
        answer, src, files = _parse_answer(answer_text)
        body_blocks = [
            {
                "type": "TextBlock",
                "text": answer,
                "wrap": True,
                "spacing": "Medium"
            }
        ]
        if src and src != "Unknown":
            body_blocks.append({
                "type": "TextBlock",
                "text": f"Source: {src}",
                "wrap": True,
                "weight": "Bolder",
                "color": "Accent",
                "spacing": "Small"
            })
        if files:
            body_blocks.append({
                "type": "TextBlock",
                "text": f"Files used: {files}",
                "wrap": True,
                "spacing": "Small"
            })
        adaptive_card = {
            "type": "AdaptiveCard",
            "body": body_blocks,
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.5"
        }
        print("[DEBUG] Outgoing Adaptive Card JSON:", json.dumps(adaptive_card, indent=2))
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
