# version 11b 
# ((Hyperlink file names))
# Made it display the files sources for the compounded questions:
#   Referenced: <Files>                <-------Hyperlink to sharepoint
#   Calculated using: <Tables>         <-------Hyperlink to sharepoint
# still the url is fixed to one file. (NEEDS WORK!)

import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse
import teams_markdown

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
    """Clean up conversation states older than 24 hours"""
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
    conv_id = turn_context.activity.conversation.id
    state = get_conversation_state(conv_id)
    state['last_activity'] = asyncio.get_event_loop().time()
    if len(conversation_states) > 100:
        cleanup_old_states()

    import ask_func
    ask_func.chat_history = state['history']
    ask_func.tool_cache    = state['cache']

    user_message = turn_context.activity.text or ""

    # Get user ID
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
    except:
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Typing indicator
    await turn_context.send_activity(Activity(type="typing"))

    try:
        # Call LLM
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # Persist state
        state['history'] = ask_func.chat_history
        state['cache']   = ask_func.tool_cache

        # Try parse JSON
        cleaned = answer_text.strip()
        if cleaned.startswith('```json'):
            cleaned = cleaned[7:].strip()
        if cleaned.startswith('```'):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3].strip()

        try:
            response_json = json.loads(cleaned)
        except json.JSONDecodeError:
            response_json = None

        # 1️⃣ Markdown fallback
        if isinstance(response_json, dict) and "content" in response_json and "source" in response_json:
            md = teams_markdown.render(
                question=user_message,
                content=response_json["content"],
                source=response_json["source"],
                files=response_json["source_details"].get("file_names", []),
                tables=response_json["source_details"].get("table_names", [])
            )
            await turn_context.send_activity(Activity(type="message", text=md))
            return

        # 2️⃣ Adaptive Card fallback
        if isinstance(response_json, dict) and response_json.get("content"):
            content_items = response_json["content"]
            source        = response_json.get("source", "")
            files         = response_json["source_details"].get("file_names", [])
            tables        = response_json["source_details"].get("table_names", [])

            # Build body_blocks
            body_blocks = []
            for item in content_items:
                t   = item.get("type")
                txt = item.get("text", "")
                if t == "heading":
                    body_blocks.append({
                        "type":"TextBlock","text":txt,
                        "wrap":True,"weight":"Bolder","size":"Large","spacing":"Medium"
                    })
                elif t == "paragraph" and not txt.startswith(("Referenced:","Calculated using:")):
                    body_blocks.append({
                        "type":"TextBlock","text":txt,
                        "wrap":True,"spacing":"Small"
                    })
                elif t == "numbered_list":
                    for i, li in enumerate(item.get("items", []), 1):
                        body_blocks.append({
                            "type":"TextBlock","text":f"{i}. {li}",
                            "wrap":True,"spacing":"Small"
                        })
                elif t == "bullet_list":
                    for li in item.get("items", []):
                        body_blocks.append({
                            "type":"TextBlock","text":f"• {li}",
                            "wrap":True,"spacing":"Small"
                        })
                elif t == "code_block":
                    body_blocks.append({
                        "type":"TextBlock",
                        "text":f"```\n{item.get('code','')}\n```",
                        "wrap":True,"fontType":"Monospace","spacing":"Medium"
                    })

            # Source container
            source_container = {
                "type":"Container","id":"sourceContainer",
                "isVisible":False,"style":"emphasis","bleed":True,
                "isScrollable":True,"items":[]
            }

            # Referenced:
            if files:
                source_container["items"].append({
                    "type":"TextBlock","text":"Referenced:","weight":"Bolder","spacing":"Small"
                })
                for f in files:
                    url = teams_markdown.link(f)
                    source_container["items"].append({
                        "type":"TextBlock","text":f"[{f}]({url})","wrap":True,"spacing":"Small"
                    })

            # Calculated using:
            if tables:
                source_container["items"].append({
                    "type":"TextBlock","text":"Calculated using:","weight":"Bolder","spacing":"Small"
                })
                for t in tables:
                    url = teams_markdown.link(t)
                    source_container["items"].append({
                        "type":"TextBlock","text":f"[{t}]({url})","wrap":True,"spacing":"Small"
                    })

            # Source line
            source_container["items"].append({
                "type":"TextBlock",
                "text":f"Source: {source}",
                "wrap":True,"weight":"Bolder","color":"Accent","spacing":"Medium"
            })

            body_blocks.append(source_container)
            # Show/Hide buttons
            body_blocks.append({
                "type":"ColumnSet","columns":[
                    {"type":"Column","id":"showSourceBtn","items":[{
                        "type":"ActionSet","actions":[{
                            "type":"Action.ToggleVisibility",
                            "title":"Show Source",
                            "targetElements":["sourceContainer","showSourceBtn","hideSourceBtn"]
                        }]
                    }]},
                    {"type":"Column","id":"hideSourceBtn","isVisible":False,"items":[{
                        "type":"ActionSet","actions":[{
                            "type":"Action.ToggleVisibility",
                            "title":"Hide Source",
                            "targetElements":["sourceContainer","showSourceBtn","hideSourceBtn"]
                        }]
                    }]}
                ]
            })

            card = {
                "type":"AdaptiveCard",
                "$schema":"http://adaptivecards.io/schemas/adaptive-card.json",
                "version":"1.5","body":body_blocks
            }
            await turn_context.send_activity(
                Activity(
                    type="message",
                    attachments=[{
                        "contentType":"application/vnd.microsoft.card.adaptive",
                        "content":card
                    }]
                )
            )
            return

        # 3️⃣ Plain-text fallback
        if match := re.search(r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$",
                              answer_text, flags=re.DOTALL):
            main_answer    = match.group(1).strip()
            source_line    = match.group(2).strip()
            appended       = match.group(3) or ""
            await turn_context.send_activity(
                Activity(type="message", text=f"{main_answer}\n\n{source_line}{appended}")
            )
        else:
            await turn_context.send_activity(
                Activity(type="message", text=answer_text)
            )

    except Exception as e:
        print("Error in bot logic:", e)
        await turn_context.send_activity(
            Activity(type="message", text=f"An error occurred: {e}")
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
