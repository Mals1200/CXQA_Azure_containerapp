import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse
import traceback

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

    # --- User identification logic ---
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

        # --- Debugging: Output raw answer, length, first 300 chars ---
        debug_text = f"DEBUG: Raw answer length: {len(answer_text)}\nFirst 300 chars:\n{answer_text[:300]}\n"
        print(debug_text)
        await turn_context.send_activity(Activity(type="message", text=f"Debug Info:\n{debug_text}"))

        # --- Parse the response as JSON if possible ---
        cleaned_answer_text = answer_text.strip()
        if cleaned_answer_text.startswith('```json'):
            cleaned_answer_text = cleaned_answer_text[7:].strip()
        if cleaned_answer_text.startswith('```'):
            cleaned_answer_text = cleaned_answer_text[3:].strip()
        if cleaned_answer_text.endswith('```'):
            cleaned_answer_text = cleaned_answer_text[:-3].strip()

        # Print/log the JSON before parse
        print("DEBUG: Attempting JSON parse. Cleaned answer text:")
        print(cleaned_answer_text[:1000])

        try:
            response_json = json.loads(cleaned_answer_text)
            # --- Count blocks and log ---
            if isinstance(response_json, dict) and "content" in response_json:
                content_items = response_json["content"]
                num_blocks = len(content_items)
                # Count list items
                num_list_items = 0
                for item in content_items:
                    if item.get("type") in ("bullet_list", "numbered_list"):
                        num_list_items += len(item.get("items", []))
                block_debug = (f"DEBUG: Card block count: {num_blocks}, "
                               f"Total list items: {num_list_items}")
                print(block_debug)
                await turn_context.send_activity(Activity(type="message", text=block_debug))

                # --- Truncate long lists for testing card render ---
                MAX_LIST_ITEMS = 8  # <== adjust as needed
                truncated = False
                for item in content_items:
                    if item.get("type") in ("bullet_list", "numbered_list"):
                        items = item.get("items", [])
                        if len(items) > MAX_LIST_ITEMS:
                            item["items"] = items[:MAX_LIST_ITEMS] + [f"...and {len(items)-MAX_LIST_ITEMS} more steps."]
                            truncated = True
                if truncated:
                    await turn_context.send_activity(Activity(type="message", text="NOTE: Some lists were truncated for debugging!"))

                # --- Build adaptive card as before ---
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
                        for list_item in item.get("items", []):
                            body_blocks.append({
                                "type": "TextBlock",
                                "text": f"• {list_item}",
                                "wrap": True,
                                "spacing": "Small"
                            })
                    elif item_type == "numbered_list":
                        for i, list_item in enumerate(item.get("items", []), 1):
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
                # Add the show/hide source section (unchanged from your original)
                # ... omitted for brevity, you can include your original code here ...

                # For debugging: add a footer
                body_blocks.append({
                    "type": "TextBlock",
                    "text": f"DEBUG: {num_blocks} blocks, {num_list_items} list items. Lists truncated: {truncated}",
                    "wrap": True,
                    "size": "Small",
                    "weight": "Lighter",
                    "color": "Accent"
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

        except Exception as json_exc:
            err_msg = f"JSON parsing/adaptive card error: {str(json_exc)}\n{traceback.format_exc()}"
            print(err_msg)
            await turn_context.send_activity(Activity(type="message", text=f"DEBUG: {err_msg}"))
            # Fallback to plaintext
            await turn_context.send_activity(Activity(type="message", text=f"Raw answer text:\n{answer_text[:1000]}"))

        # --- Legacy fallback: send as plain text if JSON parsing fails ---
        await turn_context.send_activity(Activity(type="message", text=answer_text[:4000]))

    except Exception as e:
        error_message = f"An error occurred: {str(e)}\n{traceback.format_exc()}"
        print(error_message)
        await turn_context.send_activity(Activity(type="message", text=error_message))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
