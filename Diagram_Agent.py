def Call_diagram_pyvis(latest_question, latest_answer, chat_history, diagram_type="directed"):
    import requests
    import json
    import io
    import threading
    from datetime import datetime
    from azure.storage.blob import BlobServiceClient
    from pyvis.network import Network
    """
    1) Calls Azure OpenAI (gpt-4o-3) to generate diagram text.
    2) Uses the PyVis library to build an interactive HTML diagram.
    3) Saves the HTML in-memory, no local .html file needed.
    4) Uploads to Azure Blob Storage.
    5) Schedules a timer to delete the blob after 5 minutes.
    6) Returns a direct download link (SAS URL).
    """

    ##################################################
    # (A) CALL AZURE OPENAI TO GET DIAGRAM TEXT
    ##################################################
    chat_history_str = str(chat_history)

    diagram_prompt = f"""
You are a diagram creation expert. Use the following information to make a {diagram_type} node-edge diagram.
Rules:
- Only use the given information to create the diagram instructions.
- Return lines like "NodeA -> NodeB" or single lines for nodes ("NodeC").
- If not enough information to create a diagram, return "There is not enough information to generate a diagram."

(The Information)

- Latest_Question:
{latest_question}

- Latest_Answer:
{latest_answer}

- Full Conversation:
{chat_history_str}
"""
    # FAKE Azure OpenAI values
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_message = "You are a helpful assistant that formats diagram instructions from user input."
    user_message = diagram_prompt

    payload = {
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 1000,
        "temperature": 0.7,
        "stream": True
    }
    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    diagram_response = ""
    try:
        with requests.post(LLM_ENDPOINT, headers=headers, json=payload, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if line_str.startswith("data: "):
                        data_str = line_str[len("data: "):]
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            if (
                                "choices" in data_json
                                and data_json["choices"]
                                and "delta" in data_json["choices"][0]
                            ):
                                content_piece = data_json["choices"][0]["delta"].get("content", "")
                                diagram_response += content_piece
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        diagram_response = f"An error occurred while creating the diagram: {e}"

    diagram_text = diagram_response.strip()
    if not diagram_text or "An error occurred" in diagram_text:
        return f"No valid diagram instructions returned:\n{diagram_text}"

    ##################################################
    # (B) PARSE TEXT INTO NODES & EDGES FOR PYVIS
    ##################################################
    lines = [ln.strip() for ln in diagram_text.split("\n") if ln.strip()]
    if not lines:
        return "There is not enough information to generate a diagram."

    diag_type_lower = diagram_type.lower()
    if diag_type_lower == "undirected":
        net = Network(directed=False)
    else:
        net = Network(directed=True)

    # Example of specialized layout
    if diag_type_lower == "hierarchical":
        net.set_options("""
        var options = {
          layout: {
            hierarchical: {
              enabled: true,
              levelSeparation: 150,
              nodeSpacing: 100,
              treeSpacing: 200,
              direction: 'UD',
              sortMethod: 'hubsize'
            }
          }
        }
        """)
    elif diag_type_lower == "forceatlas":
        net.force_atlas_2based()

    for line in lines:
        if "->" in line:
            parts = line.split("->")
            if len(parts) == 2:
                node_a = parts[0].strip()
                node_b = parts[1].strip()
                net.add_node(node_a, label=node_a)
                net.add_node(node_b, label=node_b)
                net.add_edge(node_a, node_b)
        else:
            net.add_node(line, label=line)

    if len(net.nodes) == 0:
        return "There is not enough information to generate a diagram."

    ##################################################
    # (C) GENERATE THE HTML STRING
    ##################################################
    # Instead of write_html(...) which expects a filename, use generate_html().
    html_str_data = net.generate_html(notebook=False)  
    # This returns a complete HTML document as a single string.
    html_bytes = html_str_data.encode("utf-8")  # convert to bytes

    ##################################################
    # (D) UPLOAD TO AZURE BLOB STORAGE
    ##################################################
    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"

    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)

    diagram_filename = f"diagram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    blob_client = container_client.get_blob_client(diagram_filename)

    blob_client.upload_blob(html_bytes, overwrite=True)

    download_link = f"{account_url}/{container_name}/{diagram_filename}?{sas_token}"

    ##################################################
    # (E) SCHEDULE AUTO-DELETE AFTER 5 MINUTES
    ##################################################
    def delete_blob_after_5():
        try:
            blob_client.delete_blob()
        except Exception:
            pass

    timer = threading.Timer(300, delete_blob_after_5)
    timer.start()

    # (F) Return the link
    return download_link
