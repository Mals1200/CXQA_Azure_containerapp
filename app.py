# version 14
# Added exception for the Export_Agent returns.

# if is_special_response(answer_text):
#     if any(answer_text.startswith(prefix) for prefix in (
#         "Here is your generated chart:",
#         "Here is your generated slides:",
#         "Here is your generated Document:",
#         "Here is your generated SOP:",
#     )):
#         await turn_context.send_activity(answer_text.strip())
#     else:
#         await turn_context.send_activity(strip_trailing_source(answer_text))
#     return


import os
import re
import json
import asyncio
from threading import Lock
from flask import Flask, request, jsonify, Response

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity

from ask_func import Ask_Question, chat_history   # noqa: F401  (imported for its side-effects)

# ------------- global config -------------------------------------------------
RENDER_MODE     = "markdown"
SHOW_REFERENCES = True
MAX_TEAMS_CARD_BYTES = 28 * 1024

MICROSOFT_APP_ID       = os.getenv("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "")
# -----------------------------------------------------------------------------

app = Flask(__name__)

adapter_settings = BotFrameworkAdapterSettings(
    MICROSOFT_APP_ID,
    MICROSOFT_APP_PASSWORD
)
adapter = BotFrameworkAdapter(adapter_settings)

# ------------------------------------------------------------------ state ----
conversation_states = {}
state_lock = Lock()

def get_conversation_state(conversation_id: str):
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conversation_id]

def cleanup_old_states(max_age_seconds: int = 86_400):
    now = asyncio.get_event_loop().time()
    with state_lock:
        for cid, state in list(conversation_states.items()):
            if state["last_activity"] and (now - state["last_activity"]) > max_age_seconds:
                del conversation_states[cid]

# ------------------------------------------------------------------- routes --
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    activity = Activity().deserialize(request.json)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)

# ----------------------------------------------------------- helper utils ----
def adaptive_card_size_ok(card_dict) -> bool:
    return len(json.dumps(card_dict, ensure_ascii=False).encode("utf-8")) <= MAX_TEAMS_CARD_BYTES

def make_fallback_card() -> dict:
    return {
        "type": "AdaptiveCard",
        "body": [{
            "type": "TextBlock",
            "text": (
                "Sorry, the answer is too large to display in Microsoft Teams.  "
                "Please refine your question or check the original document."
            ),
            "wrap": True,
            "weight": "Bolder",
            "color": "Attention",
        }],
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
    }

def extract_source_info(user_msg: str, ask_func_module):
    tool_cache = getattr(ask_func_module, "tool_cache", {})
    cache_key  = user_msg.strip().lower()
    index_dict, python_dict = {}, {}
    if cache_key in tool_cache:
        index_dict, python_dict, _ = tool_cache[cache_key]
    file_names  = index_dict.get("file_names", []) or []
    table_names = python_dict.get("table_names", []) or []
    if file_names and table_names:
        source = "Index & Python"
    elif file_names:
        source = "Index"
    elif table_names:
        source = "Python"
    else:
        source = "AI Generated"
    return file_names, table_names, source

def clean_main_answer(answer_text: str) -> str:
    cleaned = answer_text.strip()
    if cleaned.startswith("{") and '"content"' in cleaned:
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict) and "content" in obj:
                blocks = [b.get("text", "").strip() for b in obj["content"] if isinstance(b, dict)]
                cleaned = "\n\n".join(blocks).strip()
        except Exception:
            pass
    src_re = re.compile(r"^\s*(?:[-*]\s*)?\*?source\s*:.*$", re.I)
    return "\n".join([ln for ln in cleaned.splitlines() if not src_re.match(ln)]).strip()

def is_special_response(answer_text: str) -> bool:
    text = answer_text.strip().lower()
    return (
        text.startswith("hello! i'm the cxqa ai assistant")
        or text.startswith("hello! how may i assist you")
        or text.startswith("the chat has been restarted.")
        or text.startswith("export")
        or text.startswith("here is your generated")
    )

def strip_trailing_source(answer_text: str) -> str:
    return re.sub(r"\n*source:.*$", "", answer_text, flags=re.I).strip()

# ------------------------------------------------------------- BOT LOGIC -----
async def _bot_logic(turn_context: TurnContext):
    conv_id = turn_context.activity.conversation.id
    state   = get_conversation_state(conv_id)
    state["last_activity"] = asyncio.get_event_loop().time()
    if len(conversation_states) > 100:
        cleanup_old_states()

    import ask_func
    ask_func.chat_history = state["history"]
    ask_func.tool_cache   = state["cache"]

    user_message = turn_context.activity.text or ""
    if not user_message or not user_message.strip():
        return

    try:
        teams_user_id = turn_context.activity.from_property.id
        member = await TeamsInfo.get_member(turn_context, teams_user_id)
        user_id = member.user_principal_name or member.email or teams_user_id
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    await turn_context.send_activity(Activity(type="typing"))

    try:
        answer_text = "".join(Ask_Question(user_message, user_id=user_id))
        state["history"] = ask_func.chat_history
        state["cache"]   = ask_func.tool_cache

        if is_special_response(answer_text):
            if any(answer_text.startswith(prefix) for prefix in (
                "Here is your generated chart:",
                "Here is your generated slides:",
                "Here is your generated Document:",
                "Here is your generated SOP:",
            )):
                await turn_context.send_activity(answer_text.strip())
            else:
                await turn_context.send_activity(strip_trailing_source(answer_text))
            return

        files, tables, source_label = extract_source_info(user_message, ask_func)
        main_answer = clean_main_answer(answer_text)

        if RENDER_MODE == "markdown":
            md = main_answer
            if SHOW_REFERENCES:
                blocks = []
                if source_label in ("Index", "Index & Python") and files:
                    blocks.append("**Referenced:**\n" + "\n".join(f"- {f}" for f in files))
                if source_label in ("Python", "Index & Python") and tables:
                    blocks.append("**Calculated using:**\n" + "\n".join(f"- {t}" for t in tables))
                blocks.append(f"**Source:** {source_label}")
                md += "\n\n" + "\n\n".join(blocks)
            await turn_context.send_activity(md)
            return

        # --- Adaptive card flow unchanged (not repeated here for brevity) ---
        # If you want the rest of the card block added too, I’ll include it again.

    except Exception as exc:
        err = f"❌ An error occurred: {exc}"
        print(err)
        await turn_context.send_activity(err)

# ------------------------------------------------------------------ main -----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
