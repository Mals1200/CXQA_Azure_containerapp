# version 5 replacement:

import os
import asyncio
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from botbuilder.core.teams import TeamsInfo
from ask_func import Ask_Question, chat_history

app = Flask(__name__)
adapter = BotFrameworkAdapter(BotFrameworkAdapterSettings(
    os.environ.get("MICROSOFT_APP_ID", ""),
    os.environ.get("MICROSOFT_APP_PASSWORD", "")
))

@app.route("/")
def home():
    return jsonify({"status": "active"})

@app.route("/api/messages", methods=["POST"])
async def messages():
    activity = Activity().deserialize(request.json)
    auth_header = request.headers.get("Authorization", "")
    
    async def _bot_logic(context: TurnContext):
        try:
            user_id = (await TeamsInfo.get_member(context, context.activity.from_property.id)).user_principal_name
        except:
            user_id = "anonymous"
        
        await context.send_activity(Activity(type="typing"))
        answer = Ask_Question(context.activity.text, user_id=user_id)
        
        source_match = re.search(r"(.*?)(Source:.*)", answer, re.DOTALL)
        if source_match:
            main_answer = source_match.group(1)
            source_info = source_match.group(2)
            await context.send_activity(Activity(
                type="message",
                text=main_answer,
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "body": [{"type": "TextBlock", "text": main_answer}],
                        "actions": [{
                            "type": "Action.ToggleVisibility",
                            "title": "Show Details",
                            "targetElements": ["sourceInfo"]
                        }]
                    }
                }]
            ))
        else:
            await context.send_activity(Activity(type="message", text=answer))
    
    await adapter.process_activity(activity, auth_header, _bot_logic)
    return Response(status=200)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
