import os
import json
from flask import Flask, request, Response

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
    MessageFactory,
    CardFactory,
)
from botbuilder.schema import Activity

from ask_func import Ask_Question

# ------------------------------------------------------------------------------
# App & Adapter setup
# ------------------------------------------------------------------------------
app = Flask(__name__)

APP_ID = os.getenv("MICROSOFT_APP_ID", "")
APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)


# ------------------------------------------------------------------------------
# Build the Adaptive Card with Show/Hide Source toggle
# ------------------------------------------------------------------------------
def build_adaptive_card(resp_json: dict) -> dict:
    body = []

    # 1) Render each content block
    for block in resp_json.get("content", []):
        t = block.get("type")
        if t == "heading":
            body.append({
                "type": "TextBlock",
                "text": block["text"],
                "size": "Large",
                "weight": "Bolder",
                "wrap": True
            })
        elif t == "paragraph":
            body.append({
                "type": "TextBlock",
                "text": block["text"],
                "wrap": True
            })
        elif t == "bullet_list":
            for item in block.get("items", []):
                body.append({
                    "type": "TextBlock",
                    "text": f"• {item}",
                    "wrap": True
                })
        elif t == "numbered_list":
            for idx, item in enumerate(block.get("items", []), 1):
                body.append({
                    "type": "TextBlock",
                    "text": f"{idx}. {item}",
                    "wrap": True
                })
        elif t == "code_block":
            body.append({
                "type": "TextBlock",
                "text": block.get("code", ""),
                "wrap": True,
                "fontType": "Monospace"
            })

    # 2) Gather source files & tables
    sd = resp_json.get("source_details", {})
    files  = sd.get("file_names", [])
    tables = sd.get("table_names", [])

    if files or tables:
        items = []
        if files:
            items.append({
                "type": "TextBlock",
                "text": "Referenced:",
                "weight": "Bolder",
                "wrap": True
            })
            for f in files:
                items.append({
                    "type": "TextBlock",
                    "text": f"- {f}",
                    "wrap": True
                })
        if tables:
            items.append({
                "type": "TextBlock",
                "text": "Calculated using:",
                "weight": "Bolder",
                "wrap": True
            })
            for t in tables:
                items.append({
                    "type": "TextBlock",
                    "text": f"- {t}",
                    "wrap": True
                })

        # hidden by default
        body.append({
            "type": "Container",
            "id": "sourceContainer",
            "isVisible": False,
            "items": items
        })

    # 3) Toggle buttons
    actions = [
        {
            "type": "Action.ToggleVisibility",
            "title": "Show Source",
            "id": "showButton",
            "targetElements": ["sourceContainer", "showButton", "hideButton"]
        },
        {
            "type": "Action.ToggleVisibility",
            "title": "Hide Source",
            "id": "hideButton",
            "isVisible": False,
            "targetElements": ["sourceContainer", "showButton", "hideButton"]
        }
    ]

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": actions
    }


# ------------------------------------------------------------------------------
# Message handler
# ------------------------------------------------------------------------------
async def on_message_activity(turn_context: TurnContext):
    user_question = turn_context.activity.text.strip()
    user_id       = turn_context.activity.from_property.id or "anonymous"

    # Run your existing pipeline
    all_chunks = list(Ask_Question(user_question, user_id))
    final_chunk = all_chunks[-1]

    # Try to parse as JSON; on failure, send plain text
    try:
        resp_json = json.loads(final_chunk)
    except json.JSONDecodeError:
        await turn_context.send_activity(MessageFactory.text(final_chunk))
        return

    card = build_adaptive_card(resp_json)
    attachment = CardFactory.adaptive_card(card)
    message = MessageFactory.attachment(attachment)
    await turn_context.send_activity(message)


# ------------------------------------------------------------------------------
# Main endpoint for Teams to POST activities
# ------------------------------------------------------------------------------
@app.route("/api/messages", methods=["POST"])
def messages():
    if request.headers.get("Content-Type", "") != "application/json":
        return Response(status=415)

    activity = Activity().deserialize(request.json)
    auth_header = request.headers.get("Authorization", "")

    task = adapter.process_activity(activity, auth_header, on_message_activity)
    return Response(status=201)


# ------------------------------------------------------------------------------
# Run locally on port 3978 by default
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3978, debug=True)
