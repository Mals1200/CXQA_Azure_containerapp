import asyncio
import logging
from flask import Flask, request, jsonify, Response
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity
from ask_func import Ask_Question, chat_history  # adjust imports as needed

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

conversation_histories = {}

def get_teams_user_id(turn_context: TurnContext) -> str:
    """Return the best available user ID from the Teams activity's from_property."""
    from_property = turn_context.activity.from_property
    if not from_property:
        return "anonymous"

    # 1) AadObjectId is typically stable if you have SSO configured
    aad_id = getattr(from_property, "aad_object_id", None)
    if aad_id:
        return aad_id

    # 2) .id often looks like "29:abcDEF..."
    if from_property.id:
        return from_property.id

    # 3) .name might hold a display name, but often it's empty
    if from_property.name:
        return from_property.name

    return "anonymous"

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

    # create or retrieve conversation history
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # Debug: print from_property dictionary
    print("DEBUG from_property:", turn_context.activity.from_property.__dict__)

    # get user ID
    user_id = get_teams_user_id(turn_context)

    # optional typing indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # generate answer
    ans_gen = Ask_Question(question=user_message, user_id=user_id)
    answer_text = "".join(ans_gen)

    # update conversation history
    conversation_histories[conversation_id] = ask_func.chat_history

    # send the final answer
    # (You may want to parse for "Source:" and attach an Adaptive Card, etc. if needed.)
    await turn_context.send_activity(Activity(type="message", text=answer_text))

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

# Example "ask" endpoint if you also need it:
@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": 'Invalid request, "question" is required.'}), 400
    question = data["question"]

    # no direct user_id in the request, so default to 'anonymous'
    ans_gen = Ask_Question(question, user_id="anonymous")
    answer_text = "".join(ans_gen)
    return jsonify({"answer": answer_text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
