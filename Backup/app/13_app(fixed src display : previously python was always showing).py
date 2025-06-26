# version 13


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
RENDER_MODE     = "markdown"        # "markdown"  or  "adaptivecard"
SHOW_REFERENCES = True              # flip to False → hide all ref/source blocks
MAX_TEAMS_CARD_BYTES = 28 * 1024    # 28 KB hard Teams limit

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
    """Return (and lazily create) per-conversation scratch space."""
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                "history": [],
                "cache": {},
                "last_activity": None,
            }
        return conversation_states[conversation_id]


def cleanup_old_states(max_age_seconds: int = 86_400):
    """Drop conversations idle for > 24 h to keep RAM steady."""
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
    """Pull cached file / table names + decide overall 'Source:' label."""
    tool_cache = getattr(ask_func_module, "tool_cache", {})
    cache_key  = user_msg.strip().lower()

    index_dict, python_dict = {}, {}
    if cache_key in tool_cache:
        index_dict, python_dict, _ = tool_cache[cache_key]

    file_names  = index_dict.get("file_names", []) or []
    table_names = python_dict.get("table_names", []) or []

    # ✅ NEW — Fully accurate based on actual content retrieved
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
    """Strip embedded JSON + any trailing 'Source:' lines → plain markdown."""
    # If response is JSON structure from ace_tools, flatten to markdown first
    cleaned = answer_text.strip()
    if cleaned.startswith("{") and '"content"' in cleaned:
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict) and "content" in obj:
                blocks = [b.get("text", "").strip() for b in obj["content"] if isinstance(b, dict)]
                cleaned = "\n\n".join(blocks).strip()
        except Exception:
            pass

    # Remove *any* line that starts with Source: (case / markdown bullet tolerant)
    src_re = re.compile(r"^\s*(?:[-*]\s*)?\*?source\s*:.*$", re.I)
    return "\n".join([ln for ln in cleaned.splitlines() if not src_re.match(ln)]).strip()


def is_special_response(answer_text: str) -> bool:
    """Detect greeting / restart / export replies that shouldn't show refs."""
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
    # ----- quick house-keeping -----
    conv_id = turn_context.activity.conversation.id
    state   = get_conversation_state(conv_id)
    state["last_activity"] = asyncio.get_event_loop().time()
    if len(conversation_states) > 100:      # prune RAM if our bot is busy
        cleanup_old_states()

    import ask_func
    ask_func.chat_history = state["history"]
    ask_func.tool_cache   = state["cache"]

    user_message = turn_context.activity.text or ""

    # ----- NEW: quietly ignore empty / whitespace system messages -----
    if not user_message or not user_message.strip():
        return

    # identify the human (best-effort)
    try:
        teams_user_id = turn_context.activity.from_property.id
        member = await TeamsInfo.get_member(turn_context, teams_user_id)
        user_id = (
            member.user_principal_name
            or member.email
            or teams_user_id
        )
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Teams "typing…" indicator
    await turn_context.send_activity(Activity(type="typing"))

    # ----- main Q&A call -----
    try:
        answer_text = "".join(Ask_Question(user_message, user_id=user_id))
        # persist history/cache for next turn
        state["history"] = ask_func.chat_history
        state["cache"]   = ask_func.tool_cache

        # greet / restart / export? – send as-is (sans "Source:" footer)
        if is_special_response(answer_text):
            await turn_context.send_activity(strip_trailing_source(answer_text))
            return

        files, tables, source_label = extract_source_info(user_message, ask_func)
        main_answer = clean_main_answer(answer_text)

        # ==============================================================
        # 1. MARKDOWN mode  (simple text message)
        # ==============================================================
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

        # ==============================================================
        # 2. ADAPTIVE-CARD mode  (rich UI)
        # ==============================================================

        # ---------- helper: detect markdown tables ----------
        def md_table(lines):
            pipe_lines = [l for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]
            if len(pipe_lines) < 2:
                return None, None, []
            header = [c.strip() for c in pipe_lines[0].strip("|").split("|")]
            rows = [
                [c.strip() for c in ln.strip("|").split("|")]
                for ln in pipe_lines[2:]
                if len([c for c in ln.strip("|").split("|")]) == len(header)
            ]
            return header, rows, pipe_lines

        card_body = []

        # ---------- optional "Download file" button ----------
        export_link = re.search(r"https?://[^\s)\]]+", main_answer)
        is_export   = main_answer.lower().startswith("here is your generated")
        if export_link and not is_export:
            url = export_link.group(0)
            main_answer = main_answer.replace(url, "").strip()
            card_body.append({
                "type": "ActionSet",
                "actions": [{"type": "Action.OpenUrl", "title": "Download File", "url": url}],
            })

        # ---------- plain text or markdown table ----------
        header, rows, tbl_lines = md_table(main_answer.splitlines())
        if header and rows:
            before_tbl = main_answer.split("|")[0].strip()
            if before_tbl:
                card_body.append({
                    "type": "TextBlock", "text": before_tbl, "wrap": True, "spacing": "Medium"
                })
            # table header
            card_body.append({
                "type": "ColumnSet",
                "columns": [{
                    "type": "Column", "width": "stretch",
                    "items": [{"type": "TextBlock", "text": h, "weight": "Bolder", "wrap": True}]
                } for h in header]
            })
            # rows
            for row in rows:
                card_body.append({
                    "type": "ColumnSet",
                    "columns": [{
                        "type": "Column", "width": "stretch",
                        "items": [{"type": "TextBlock", "text": c, "wrap": True}]
                    } for c in row]
                })
            after_tbl = main_answer.split(tbl_lines[-1])[-1].strip()
            if after_tbl:
                card_body.append({"type": "TextBlock", "text": after_tbl, "wrap": True})
        else:
            if main_answer:
                card_body.append({"type": "TextBlock", "text": main_answer, "wrap": True})

        # ---------- export response footer ----------
        if is_export and export_link:
            card_body.append({
                "type": "ActionSet",
                "actions": [{"type": "Action.OpenUrl", "title": "Download File", "url": export_link.group(0)}],
            })

        # ---------- reference / source toggle ----------
        if not is_export and SHOW_REFERENCES:
            source_container = {
                "type": "Container",
                "id": "sourceContainer",
                "isVisible": False,
                "style": "emphasis",
                "bleed": True,
                "maxHeight": "500px",
                "isScrollable": True,
                "items": [],
            }
            if source_label in ("Index", "Index & Python"):
                source_container["items"].append({
                    "type": "TextBlock", "text": "Referenced:", "weight": "Bolder", "wrap": True
                })
                source_container["items"].extend(
                    {"type": "TextBlock", "text": f, "wrap": True, "color": "Accent"} for f in (files or ["(None)"])
                )
            if source_label in ("Python", "Index & Python"):
                source_container["items"].append({
                    "type": "TextBlock", "text": "Calculated using:", "weight": "Bolder", "wrap": True
                })
                source_container["items"].extend(
                    {"type": "TextBlock", "text": t, "wrap": True, "color": "Accent"} for t in (tables or ["(None)"])
                )
            source_container["items"].append({
                "type": "TextBlock", "text": f"Source: {source_label}", "weight": "Bolder", "color": "Accent",
            })

            toggle_row = {
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "id": "showSourceBtn",
                        "isVisible": True,
                        "items": [{
                            "type": "ActionSet",
                            "actions": [{
                                "type": "Action.ToggleVisibility",
                                "title": "Show Source",
                                "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"],
                            }],
                        }],
                    },
                    {
                        "type": "Column",
                        "id": "hideSourceBtn",
                        "isVisible": False,
                        "items": [{
                            "type": "ActionSet",
                            "actions": [{
                                "type": "Action.ToggleVisibility",
                                "title": "Hide Source",
                                "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"],
                            }],
                        }],
                    },
                ],
            }

            card_body.extend([source_container, toggle_row])

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": card_body,
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.5",
        }

        # fallback if we exceed 28 KB
        if not adaptive_card_size_ok(adaptive_card):
            adaptive_card = make_fallback_card()

        await turn_context.send_activity(
            Activity(
                type="message",
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card,
                }],
            )
        )

    # ----------------------------- any internal error → user + log ----------
    except Exception as exc:
        err = f"❌ An error occurred: {exc}"
        print(err)
        await turn_context.send_activity(err)


# ------------------------------------------------------------------ main -----
if __name__ == "__main__":
    # Gunicorn launches this module; still handy for local `python app.py`
    app.run(host="0.0.0.0", port=80)
