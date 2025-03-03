def Ask_Question(question):
    global chat_history

    ################################################################
    # 1) RESTART CHAT
    ################################################################
    if question.lower() == "restart chat":
        # Clear the history and return message immediately
        chat_history = []
        return "The chat has been restarted."

    ################################################################
    # 2) EXPORT PPT
    ################################################################
    if question.lower() == "export ppt":
        # Do NOT append "export ppt" to chat_history
        from PPT_Agent import Call_PPT

        # If needed, you can reference the last user question/assistant answer
        # from chat_history[-2] or chat_history[-1]. Just be sure the history has
        # something in it or handle the case if it's empty.
        latest_question = chat_history[-2] if len(chat_history) >= 2 else ""
        latest_answer   = chat_history[-1] if len(chat_history) >= 1 else ""

        answer = Call_PPT(
            latest_question=latest_question,
            latest_answer=latest_answer,
            chat_history=chat_history
        )
        return answer

    ################################################################
    # 3) EXPORT DIAGRAM
    ################################################################
    if question.lower().startswith("export diagram"):
        # Do NOT append "export diagram ..." to chat_history
        from Diagram_Agent import Call_diagram_pyvis

        # Parse diagram type (directed/undirected/hierarchical) from user input
        parts = question.split()
        if len(parts) >= 3:
            diagram_type = parts[2].lower()
            if diagram_type not in ["directed", "undirected", "hierarchical"]:
                diagram_type = "directed"
        else:
            diagram_type = "directed"

        # Again, retrieve the latest question/answer if available
        latest_question = chat_history[-2] if len(chat_history) >= 2 else ""
        latest_answer   = chat_history[-1] if len(chat_history) >= 1 else ""

        answer = Call_diagram_pyvis(
            latest_question=latest_question,
            latest_answer=latest_answer,
            chat_history=chat_history,
            diagram_type=diagram_type
        )
        return answer

    ################################################################
    # 4) DEFAULT CASE: APPEND QUESTION & RESPOND
    ################################################################
    # At this point, we know the user hasn't asked for "restart chat",
    # "export ppt", or "export diagram"
    
    # Append the user's question to history
    chat_history.append(f"User: {question}")

    # Your normal agent logic
    answer = agent_answer(question)

    # Append the assistant's response
    chat_history.append(f"Assistant: {answer}")

    # Trim the history if needed
    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2
    chat_history = chat_history[-max_entries:]

    # LOGGING SECTION (optional, as in your original code)
    from datetime import datetime
    from azure.storage.blob import BlobServiceClient

    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)

    target_folder_path = "UI/2024-11-20_142337_UTC/cxqa_data/logs/"
    date_str = datetime.now().strftime("%Y_%m_%d")
    log_filename = f"logs_{date_str}.csv"
    blob_name = target_folder_path + log_filename
    blob_client = container_client.get_blob_client(blob_name)

    try:
        existing_data = blob_client.download_blob().readall().decode("utf-8")
        lines = existing_data.strip().split("\n")
        # Check if CSV header is present
        if not lines or not lines[0].startswith("time,question,answer,user_id"):
            lines = ["time,question,answer,user_id"]
    except:
        lines = ["time,question,answer,user_id"]

    current_time = datetime.now().strftime("%H:%M:%S")
    row = [
        current_time,
        question.replace('"','""'),
        answer.replace('"','""'),
        "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))

    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    # Return the assistant's response
    return answer
