# Simple Teams Bot – minimal version focused on plain-text answers
# ───────────────────────────────────────────────────────────────────────────────
# This implementation keeps only what is strictly required:
# • Flask API with Bot Framework adapter
# • Calls ask_func.Ask_Question and formats the response as plain text
# • Displays: answer, Source line, Calculated using: <tables>, Referenced: <files>
#
# Environment variables expected (as before):
#   MICROSOFT_APP_ID       – Bot ID issued by Azure Bot registration
#   MICROSOFT_APP_PASSWORD – Bot password / client secret

import os
import asyncio
import json
import re
from threading import Lock

from flask import Flask, request, Response, jsonify
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history as global_chat_history, tool_cache as global_tool_cache

# ──────────────────────── Flask & Bot Adapter setup ───────────────────────────
app = Flask(__name__)

MICROSOFT_APP_ID       = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter          = BotFrameworkAdapter(adapter_settings)

# ─────────────────────── Conversation-state (very light) ──────────────────────
conversation_states = {}  # conversation_id → {history, cache, last_activity}
state_lock = Lock()

def _get_state(cid: str):
    with state_lock:
        st = conversation_states.setdefault(cid, {"history": [], "cache": {}, "last_activity": None})
    return st

# ───────────────────────────── Helper functions ───────────────────────────────

def _render_content(blocks):
    """Extracts human-readable text from the structured `content` array produced
    by ask_func (omitting internal Calculated/Referenced bullets)."""
    out_lines = []
    skip = False
    for blk in blocks or []:
        bt = blk.get("type", "")
        txt = blk.get("text", "")
        if bt == "paragraph" and txt.lower().startswith(("calculated using", "referenced")):
            skip = True
            continue
        if skip and bt in ("paragraph", "bullet_list", "numbered_list"):
            continue  # keep skipping until new section
        if bt == "heading":
            out_lines.append(txt.strip())
            out_lines.append("")
        elif bt == "paragraph":
            out_lines.append(txt.strip())
            out_lines.append("")
        elif bt == "bullet_list":
            out_lines.extend(f"• {itm}" for itm in blk.get("items", []))
            out_lines.append("")
        elif bt == "numbered_list":
            out_lines.extend(f"{i}. {itm}" for i, itm in enumerate(blk.get("items", []), 1))
            out_lines.append("")
    return "\n".join(out_lines).strip()


def _parse_answer(full_answer):
    """Return tuple (answer_text, source_type, tables_used, files_used).
    Supports both the new JSON schema and legacy plain-text answers."""

    # 1) Try JSON format (preferred)
    try:
        cleaned = full_answer.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:].strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        js = json.loads(cleaned)
        if isinstance(js, dict) and "content" in js and "source" in js:
            answer_text = _render_content(js.get("content", [])) or cleaned
            source_type = js.get("source", "Unknown")
            sd = js.get("source_details", {})
            tables = sd.get("table_names", [])
            files  = sd.get("file_names", [])
            return answer_text, source_type, tables, files
    except Exception:
        pass

    # 2) Legacy plain-text parsing
    answer_text, source_type = full_answer, "Unknown"
    tables, files = [], []

    # Extract source line if present
    m = re.search(r"^(.+?)\s*Source:\s*(.+)$", full_answer, flags=re.IGNORECASE | re.DOTALL)
    if m:
        answer_text = m.group(1).strip()
        rest       = m.group(2).strip()
        source_type_line, *rest_lines = rest.splitlines()
        source_type = source_type_line.strip()
        joined_rest = "\n".join(rest_lines)
        # Find Calculated/Referenced sections
        cal_match = re.search(r"Calculated using:\s*(.+)", joined_rest, flags=re.IGNORECASE)
        ref_match = re.search(r"Referenced:\s*(.+)", joined_rest, flags=re.IGNORECASE)
        if cal_match:
            tables = [t.strip() for t in cal_match.group(1).split(",") if t.strip()]
        if ref_match:
            files  = [f.strip() for f in ref_match.group(1).split(",") if f.strip()]

    return answer_text, source_type, tables, files

# ───────────────────────────── Flask endpoints ────────────────────────────────

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "CXQA bot API is running"}), 200

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
        return Response(status=200)
    finally:
        loop.close()

# ───────────────────────────── Bot logic ──────────────────────────────────────

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state           = _get_state(conversation_id)
    state["last_activity"] = asyncio.get_event_loop().time()

    # Sync ask_func global vars with per-conversation state
    global_chat_history[:] = state["history"]
    global_tool_cache.clear()
    global_tool_cache.update(state["cache"])

    user_message = turn_context.activity.text or ""

    # show typing indicator
    await turn_context.send_activity(Activity(type="typing"))

    # Determine a "user_id" (email preferred)
    try:
        teams_member = await TeamsInfo.get_member(turn_context, turn_context.activity.from_property.id)
        user_id = teams_member.user_principal_name or teams_member.email or teams_member.id
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Call main QA function
    try:
        tokens = Ask_Question(user_message, user_id=user_id)
        full_answer = "".join(tokens)
    except Exception as e:
        await turn_context.send_activity(Activity(type="message", text=f"Error: {e}"))
        return

    # Parse output to desired format
    answer_text, source_type, tables, files = _parse_answer(full_answer)

    # Build final plain-text response
    lines = [answer_text, f"Source: {source_type}"]
    if tables:
        lines.append("Calculated using: " + ", ".join(tables))
    if files:
        lines.append("Referenced: " + ", ".join(files))

    final_text = "\n".join(lines)

    # Send the message
    await turn_context.send_activity(Activity(type="message", text=final_text))

    # Persist updated state
    state["history"] = global_chat_history.copy()
    state["cache"]   = global_tool_cache.copy()

# ────────────────────────── Application entrypoint ───────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
