##########################################################
# version(1)
##########################################################
def Ask_Question(question):
    """
    Top-level function:
    - If "export", do export (Call_Export from Export_Agent.py)
    - If "restart chat", clear
    - Otherwise, normal Q&A logic
    Yields the final answer or export outcome.
    """
    global chat_history
    q_lower = question.lower().strip()

    # 1) Handle export requests
    if q_lower.startswith("export"):
        from Export_Agent import Call_Export
        instructions = question[6:].strip()  # everything after "export"

        # If chat_history is too short:
        latest_answer = "No previous answer available."
        latest_question = "No previous question available."
        if len(chat_history) >= 2:
            # Typically chat_history appends in order: [User: X, Assistant: Y, ...]
            latest_answer = chat_history[-1]
            latest_question = chat_history[-2]
        elif len(chat_history) == 1:
            latest_question = chat_history[-1]

        export_result = Call_Export(latest_question, latest_answer, chat_history, instructions)
        yield export_result
        return

    # 2) Handle "restart chat"
    if (q_lower == "restart chat") or (q_lower == "reset chat") or (q_lower == "restart the chat") or (q_lower == "reset the chat") or (q_lower == "start over"):
        chat_history.clear()
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # 3) Normal Q&A
    chat_history.append(f"User: {question}")
    answer_text = agent_answer(question)
    chat_history.append(f"Assistant: {answer_text}")

    # Keep chat_history from growing too large
    if len(chat_history) > 12:
        chat_history = chat_history[-12:]

    # 4) Logging
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
        if not lines or not lines[0].startswith("time,question,answer,user_id"):
            lines = ["time,question,answer,user_id"]
    except:
        lines = ["time,question,answer,user_id"]

    current_time = datetime.now().strftime("%H:%M:%S")
    row = [
        current_time,
        question.replace('"','""'),
        answer_text.replace('"','""'),
        "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))
    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    # 5) Return the final answer
    yield answer_text








###########################################################
# version(2) Added email capture in the logging from teams
###########################################################
def Ask_Question(question, user_email="anonymous"):
    """
    Top-level function:
    - If "export", do export (Call_Export from Export_Agent.py)
    - If "restart chat", clear
    - Otherwise, normal Q&A logic
    Yields the final answer or export outcome.
    """
    global chat_history
    q_lower = question.lower().strip()

    # 1) Handle export requests
    if q_lower.startswith("export"):
        from Export_Agent import Call_Export
        instructions = question[6:].strip()  # everything after "export"

        # If chat_history is too short:
        latest_answer = "No previous answer available."
        latest_question = "No previous question available."
        if len(chat_history) >= 2:
            latest_answer = chat_history[-1]
            latest_question = chat_history[-2]
        elif len(chat_history) == 1:
            latest_question = chat_history[-1]

        export_result = Call_Export(latest_question, latest_answer, chat_history, instructions)
        yield export_result
        return

    # 2) Handle "restart chat"
    if (q_lower == "restart chat") or (q_lower == "reset chat") or (q_lower == "restart the chat") or (q_lower == "reset the chat") or (q_lower == "start over"):
        chat_history.clear()
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # 3) Normal Q&A
    chat_history.append(f"User: {question}")
    answer_text = agent_answer(question)
    chat_history.append(f"Assistant: {answer_text}")

    # Keep chat_history from growing too large
    if len(chat_history) > 12:
        chat_history = chat_history[-12:]

    # 4) Logging
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
        # Make sure the CSV header is present
        if not lines or not lines[0].startswith("time,question,answer,user_id"):
            lines = ["time,question,answer,user_id"]
    except:
        # If blob doesn't exist yet, create with CSV header
        lines = ["time,question,answer,user_id"]

    current_time = datetime.now().strftime("%H:%M:%S")
    row = [
        current_time,
        question.replace('"','""'),
        answer_text.replace('"','""'),
        user_email.replace('"','""')  # <-- Use user_email here
    ]
    lines.append(",".join(f'"{x}"' for x in row))
    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    # 5) Return the final answer
    yield answer_text



##########################################################################################################
# version(3) The ASK_Question function that now uses a seperate logging function and add topic to the logs
###########################################################################################################
def Ask_Question(question, user_email="anonymous"):
    """
    Top-level function:
    - If "export", do export (Call_Export from Export_Agent.py)
    - If "restart chat", clear
    - Otherwise, normal Q&A logic
    Yields the final answer or export outcome.
    Accepts a user_email parameter for logging.
    """
    global chat_history
    q_lower = question.lower().strip()

    # 1) Handle export requests
    if q_lower.startswith("export"):
        from Export_Agent import Call_Export
        instructions = question[6:].strip()  # everything after "export"

        # If chat_history is too short:
        latest_answer = "No previous answer available."
        latest_question = "No previous question available."
        if len(chat_history) >= 2:
            # Typically chat_history appends in order: [User: X, Assistant: Y, ...]
            latest_answer = chat_history[-1]
            latest_question = chat_history[-2]
        elif len(chat_history) == 1:
            latest_question = chat_history[-1]

        export_result = Call_Export(latest_question, latest_answer, chat_history, instructions)
        yield export_result
        return

    # 2) Handle "restart chat"
    if (
        q_lower == "restart chat"
        or q_lower == "reset chat"
        or q_lower == "restart the chat"
        or q_lower == "reset the chat"
        or q_lower == "start over"
    ):
        chat_history.clear()
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # 3) Normal Q&A
    chat_history.append(f"User: {question}")
    answer_text = agent_answer(question)
    chat_history.append(f"Assistant: {answer_text}")

    # Keep chat_history from growing too large
    if len(chat_history) > 12:
        chat_history = chat_history[-12:]

    # 4) Logging
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
        if not lines or not lines[0].startswith("time,question,answer,user_id"):
            lines = ["time,question,answer,user_id"]
    except:
        lines = ["time,question,answer,user_id"]

    current_time = datetime.now().strftime("%H:%M:%S")
    row = [
        current_time,
        question.replace('"','""'),
        answer_text.replace('"','""'),
        user_email.replace('"','""')  # Store the actual user_email instead of "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))
    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    # 5) Return the final answer
    yield answer_text
