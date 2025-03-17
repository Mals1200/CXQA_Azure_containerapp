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
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # EXAMPLE: You can try to get an email or AAD object ID from Teams:
    # user_id = turn_context.activity.from_property.aad_object_id or "anonymous"
    # or simpler: use the .id or .name if email is not available:
    user_id = turn_context.activity.from_property.id or "anonymous"

    # 1) built-in Teams typing indicator (ephemeral)
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # 2) get answer
    ans_gen = ask_func.Ask_Question(user_message, user_id=user_id)
    answer_text = "".join(ans_gen)

    # 3) store updated conversation
    conversation_histories[conversation_id] = ask_func.chat_history

    # 4) (Your existing code to send the answer as an Adaptive Card or plain text)
    await turn_context.send_activity(Activity(type="message", text=answer_text))
