import os
import asyncio
import logging
import re

from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity

from ask_func import Ask_Question, chat_history

# ——— Configure logging ——————————————————————————————————————————————
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ——— Flask + Bot adapter setup ————————————————————————————————————————
app = Flask(__name__)

MICROSOFT_APP_ID       = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# ——— In‑memory per‑conversation store ———————————————————————————————
# Maps conversation_id → { "history": [...], "cache": {...} }
conversation_store = {}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body        = request.json
    activity    = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            adapter.process_activity(activity, auth_header, _bot_logic)
        )
    finally:
        loop.close()

    return Response(status=200)


async def _bot_logic(turn_context: TurnContext):
    conv_id = turn_context.activity.conversation.id
    # Initialize if first time
    if conv_id not in conversation_store:
        conversation_store[conv_id] = {"history": [], "cache": {}}

    # Point your ask_func.chat_history at this conversation's history list
    import ask_func
    ask_func.chat_history = conversation_store[conv_id]["history"]

    user_message = turn_context.activity.text or ""
    if not user_message.strip():
        return

    # ——— Resolve a robust user_id via TeamsInfo ————————————————————————
    user_id = "anonymous"
    try:
        teams_id = turn_context.activity.from_property.id
        member  = await TeamsInfo.get_member(turn_context, teams_id)
        if getattr(member, "user_principal_name", None):
            user_id = member.user_principal_name
        elif getattr(member, "email", None):
            user_id = member.email
        else:
            user_id = teams_id
    except Exception:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # ——— Show typing indicator —————————————————————————————————————
    await turn_context.send_activity(Activity(type="typing"))

    # ——— Call Ask_Question, passing in both history & cache —————————
    answer_text = ""
    try:
        for chunk in Ask_Question(
            question     = user_message,
            user_id      = user_id,
            chat_history = ask_func.chat_history,
            tool_cache   = conversation_store[conv_id]["cache"],
        ):
            answer_text += str(chunk)
    except Exception as e:
        logger.error(f"Error in Ask_Question: {e}", exc_info=True)
        answer_text = f"I'm sorry—something went wrong. ({e})"

    # Persist the updated history back into our store
    conversation_store[conv_id]["history"] = ask_func.chat_history

    # ——— Ensure we have a Source line —————————————————————————————————
    if "Source:" not in answer_text:
        answer_text += "\n\nSource: Ai Generated"

    # ——— Send the reply (handles adaptive‑card toggle on "Source:" lines) —————
    if not answer_text.strip():
        await turn_context.send_activity(
            Activity(type="message", text="I couldn't generate a response.")
        )
        return

    # chunk very long replies
    if len(answer_text) > 25000:
        parts = [answer_text[i : i + 15000] for i in range(0, len(answer_text), 15000)]
        for idx, part in enumerate(parts):
            prefix = f"(Part {idx+1}/{len(parts)}) " if len(parts) > 1 else ""
            await turn_context.send_activity(Activity(type="message", text=prefix + part))
        return

    # look for a "Source:" line plus optional details block
    source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)

    if match:
        main_answer = match.group(1).strip()
        source_line = match.group(2).strip()
        appended_details = match.group(3) if match.group(3) else ""

        body_blocks = [
            {
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            },
            {
                "type": "TextBlock",
                "text": source_line,
                "wrap": True,
                "id": "sourceLineBlock",
                "isVisible": True  # Source line visible by default
            }
        ]

        # Always add a source details block, even if empty
        if appended_details:
            body_blocks.append({
                "type": "TextBlock",
                "text": appended_details.strip(),
                "wrap": True,
                "id": "sourceBlock",
                "isVisible": False
            })
        else:
            # Add an empty source details block
            body_blocks.append({
                "type": "TextBlock",
                "text": "No additional details available.",
                "wrap": True,
                "id": "sourceBlock",
                "isVisible": False
            })
            
        # Always include the toggle button
        actions = [
            {
                "type": "Action.ToggleVisibility",
                "title": "Show Source",  # Original button text
                "targetElements": ["sourceBlock"]  # Only toggle details since source is visible
            }
        ]

        adaptive_card = {
            "type": "AdaptiveCard",
            "body": body_blocks,
            "actions": actions,
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.2"
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
        await turn_context.send_activity(Activity(type="message", text=answer_text))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
