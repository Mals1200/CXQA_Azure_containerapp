async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    if conversation_id not in conversation_histories:
        conversation_histories[conversation_id] = []

    import ask_func
    ask_func.chat_history = conversation_histories[conversation_id]

    user_message = turn_context.activity.text or ""

    # --------------------------------------------------------------------------
    # EXTRACT USER EMAIL / UPN FROM TEAMS (OR FALL BACK IF NOT FOUND)
    # --------------------------------------------------------------------------
    user_id = "anonymous"

    from_prop = turn_context.activity.from_property
    channel_data = turn_context.activity.channel_data or {}

    # 1) Check if there's a typical Teams field in channelData
    #    (e.g., "teamsUser" -> "userPrincipalName").
    teams_user = channel_data.get("teamsUser", {})
    if isinstance(teams_user, dict):
        possible_upn = teams_user.get("userPrincipalName")
        if possible_upn and "@" in possible_upn:
            user_id = possible_upn

    # 2) If not found yet, check "from_property" fields
    if user_id == "anonymous" and from_prop:
        # Try userPrincipalName
        if hasattr(from_prop, "user_principal_name") and from_prop.user_principal_name:
            user_id = from_prop.user_principal_name

        # If still not found, check additionalProperties
        elif getattr(from_prop, "additional_properties", None):
            extra_props = from_prop.additional_properties
            upn_extra = extra_props.get("userPrincipalName") or extra_props.get("email")
            if upn_extra and "@" in upn_extra:
                user_id = upn_extra

        # If we never found a valid email, try using aadObjectId at least
        if user_id == "anonymous":
            if hasattr(from_prop, "aadObjectId") and from_prop.aadObjectId:
                user_id = from_prop.aadObjectId

        # Finally, if we still have nothing, fallback to from_prop.id
        if user_id == "anonymous":
            if from_prop.id:
                user_id = from_prop.id

    # --------------------------------------------------------------------------
    # Send "typing" indicator to Teams
    # --------------------------------------------------------------------------
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    # --------------------------------------------------------------------------
    # Pass user_id to Ask_Question so it gets logged properly
    # --------------------------------------------------------------------------
    ans_gen = Ask_Question(user_message, user_id=user_id)
    answer_text = "".join(ans_gen)

    # --------------------------------------------------------------------------
    # Update conversation history for this conversation ID
    # --------------------------------------------------------------------------
    conversation_histories[conversation_id] = ask_func.chat_history

    # --------------------------------------------------------------------------
    # Build an optional Adaptive Card with a Show/Hide Source button
    # --------------------------------------------------------------------------
    import re
    source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
    match = re.search(source_pattern, answer_text, flags=re.DOTALL)
    if match:
        main_answer = match.group(1).strip()
        source_line = match.group(2).strip()
        appended_details = match.group(3) if match.group(3) else ""
    else:
        main_answer = answer_text
        source_line = ""
        appended_details = ""

    if source_line:
        # Hide both the source line and appended details behind the same toggle
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
                "isVisible": False
            }
        ]

        if appended_details:
            body_blocks.append({
                "type": "TextBlock",
                "text": appended_details.strip(),
                "wrap": True,
                "id": "sourceBlock",
                "isVisible": False
            })

        actions = []
        if appended_details or source_line:
            actions = [
                {
                    "type": "Action.ToggleVisibility",
                    "title": "Show Source",
                    "targetElements": ["sourceLineBlock", "sourceBlock"]
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
        # No "Source:" line, just return the plain text
        await turn_context.send_activity(Activity(type="message", text=main_answer))
