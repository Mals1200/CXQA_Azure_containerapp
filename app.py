# app.py (minimal edits to always include the source toggle)

import os
import asyncio
from threading import Lock
import json
import urllib.parse

import teams_markdown
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history, tool_cache

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
        now = asyncio.get_event_loop().time()
        for cid, st in list(conversation_states.items()):
            if st['last_activity'] and (now - st['last_activity']) > 86400:
                del conversation_states[cid]

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

    # restore chat history & cache
    chat_history[:] = state['history']
    tool_cache.clear()
    tool_cache.update(state['cache'])

    user_message = turn_context.activity.text or ""
    user_id = "anonymous"
    try:
        tid = turn_context.activity.from_property.id
        m = await TeamsInfo.get_member(turn_context, tid)
        user_id = getattr(m, 'user_principal_name', None) or getattr(m, 'email', None) or tid
    except:
        user_id = turn_context.activity.from_property.id or "anonymous"

    await turn_context.send_activity(Activity(type="typing"))

    try:
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # persist
        state['history'] = chat_history.copy()
        state['cache']   = tool_cache.copy()

        # strip fences
        cleaned = answer_text.strip()
        for f in ('```json', '```'):
            if cleaned.startswith(f):
                cleaned = cleaned[len(f):].strip()
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3].strip()

        # try JSON
        try:
            resp = json.loads(cleaned)
        except:
            resp = None

        # prepare content & source
        if isinstance(resp, dict) and 'content' in resp and 'source' in resp:
            content = resp['content']
            source  = resp['source']
            files   = resp.get('source_details', {}).get('file_names', [])
            tables  = resp.get('source_details', {}).get('table_names', [])
        else:
            # fallback: wrap full text and extract trailing "Source:" if present
            content = [{"type":"paragraph","text":answer_text}]
            last = answer_text.strip().splitlines()[-1]
            source = last[len("Source:"):].strip() if last.startswith("Source:") else ""
            files = []
            tables = []

        # build adaptive card
        body = []
        for item in content:
            t = item.get("type")
            txt = item.get("text","")
            if t=="heading":
                body.append({"type":"TextBlock","text":txt,"wrap":True,"weight":"Bolder","size":"Large"})
            else:
                body.append({"type":"TextBlock","text":txt,"wrap":True})

        # hidden source container
        items = []
        if files:
            items.append({"type":"TextBlock","text":"Referenced:","weight":"Bolder"})
            for f in files:
                items.append({"type":"TextBlock","text":f"[{f}]({teams_markdown.link(f)})","wrap":True})
        if tables:
            items.append({"type":"TextBlock","text":"Calculated using:","weight":"Bolder"})
            for t in tables:
                items.append({"type":"TextBlock","text":f"[{t}]({teams_markdown.link(t)})","wrap":True})
        if source:
            items.append({"type":"TextBlock","text":f"Source: {source}","weight":"Bolder","color":"Accent","wrap":True})

        source_container = {"type":"Container","id":"sourceContainer","isVisible":False,"items":items}
        body.append(source_container)

        # toggle buttons
        body.append({
            "type":"ColumnSet","columns":[
                {"type":"Column","id":"showBtn","items":[
                    {"type":"ActionSet","actions":[
                        {"type":"Action.ToggleVisibility","title":"Show Source","targetElements":["sourceContainer","showBtn","hideBtn"]}
                    ]}
                ]},
                {"type":"Column","id":"hideBtn","isVisible":False,"items":[
                    {"type":"ActionSet","actions":[
                        {"type":"Action.ToggleVisibility","title":"Hide Source","targetElements":["sourceContainer","showBtn","hideBtn"]}
                    ]}
                ]}
            ]
        })

        card = {
            "type":"AdaptiveCard",
            "$schema":"http://adaptivecards.io/schemas/adaptive-card.json",
            "version":"1.5",
            "body":body
        }

        await turn_context.send_activity(
            Activity(type="message",
                     attachments=[{"contentType":"application/vnd.microsoft.card.adaptive","content":card}])
        )

    except Exception as e:
        print("Error:", e)
        await turn_context.send_activity(Activity(type="message", text=f"An error occurred: {e}"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)

