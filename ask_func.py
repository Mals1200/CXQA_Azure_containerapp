import os
import io
import re
import json
import logging
import warnings
import requests
import contextlib
import pandas as pd
from io import BytesIO, StringIO
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import csv

logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []

# -------------------------------------------------------------------
# Helper: Stream OpenAI from Azure
# -------------------------------------------------------------------
def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    with requests.post(endpoint, headers=headers, json=payload, stream=True) as response:
        response.raise_for_status()
        final_text = ""
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
                            if print_stream:
                                print(content_piece, end="", flush=True)
                            final_text += content_piece
                    except json.JSONDecodeError:
                        pass
        if print_stream:
            print()
    return final_text


# -------------------------------------------------------------------
# References
# -------------------------------------------------------------------
def references_tabular_data(question, tables_text):
    q_tokens = set(re.findall(r"\w+", question.lower()))
    t_tokens = set(re.findall(r"\w+", tables_text.lower()))
    return len(q_tokens.intersection(t_tokens)) > 0

# -------------------------------------------------------------------
# Tool 1 (Index)
# -------------------------------------------------------------------
def tool_1_index_search(user_question, top_k=5):
    """
    Returns a dict: {"top_k": <combined search text or "No information">}
    """
    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    INDEX_NAME = "cxqa-ind-v6"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        results = search_client.search(
            search_text=user_question,
            query_type="semantic",
            semantic_configuration_name="azureml-default",
            top=top_k,
            include_total_count=False
        )
        contents = []
        for r in results:
            c = r.get("content", "").strip()
            if c:
                contents.append(c)
        if not contents:
            return {"top_k": "No information"}
        combined = "\n\n---\n\n".join(contents)
        return {"top_k": combined}
    except Exception as e:
        return {"top_k": f"Error in Tool1 (Index Search): {str(e)}"}

# -------------------------------------------------------------------
# Tool 2 (Python)
# -------------------------------------------------------------------
def tool_2_code_run(user_question):
    """
    Returns a dict: {"result": <execution result>, "code": <the generated code>} 
    or "No information" if question not referencing data or code can't be generated.
    """
    # If no reference to data, skip
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = f"""
You are a python expert. Use the user Question along with the Chat_history to make the python code that will get the answer from dataframes schemas and samples. 
Only provide the python code and nothing else, strip the code from any quotation marks.
Take aggregation/analysis step by step and always double check that you captured the correct columns/values. 
Don't give examples, only provide the actual code. If you can't provide the code, say "404" and make sure it's a string.

**Rules**:
1. Only use tables columns that exist, and do not makeup anything. 
2. Only return pure Python code that is functional and ready to be executed, including the imports if needed.
3. Always make code that returns a print statement that answers the question.

User question:
{user_question}

Dataframes schemas:
{SCHEMA_TEXT}

Dataframes samples:
{SAMPLE_TEXT}

Chat_history:
{chat_history}
"""

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 1200,
        "temperature": 0.7,
        "stream": True
    }

    try:
        code_str = ""
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
                                code_str += content_piece
                        except json.JSONDecodeError:
                            pass

        code_str = code_str.strip()
        if not code_str or "404" in code_str:
            return {"result": "No information", "code": ""}

        # Execute
        execution_result = execute_generated_code(code_str)
        return {"result": execution_result, "code": code_str}

    except Exception as ex:
        return {
            "result": f"Error in Tool2 (Code Generation/Execution): {str(ex)}",
            "code": ""
        }


def execute_generated_code(code_str):
    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
    target_folder_path = "UI/2024-11-20_142337_UTC/cxqa_data/tabular/"

    try:
        blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
        container_client = blob_service_client.get_container_client(container_name)

        dataframes = {}
        blobs = container_client.list_blobs(name_starts_with=target_folder_path)

        for blob in blobs:
            file_name = blob.name.split('/')[-1]
            blob_client = container_client.get_blob_client(blob.name)
            blob_data = blob_client.download_blob().readall()

            if file_name.endswith('.xlsx') or file_name.endswith('.xls'):
                df = pd.read_excel(io.BytesIO(blob_data))
            elif file_name.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(blob_data))
            else:
                continue

            dataframes[file_name] = df

        code_modified = code_str.replace("pd.read_excel(", "dataframes.get(")
        code_modified = code_modified.replace("pd.read_csv(", "dataframes.get(")

        output_buffer = StringIO()
        with contextlib.redirect_stdout(output_buffer):
            local_vars = {
                "dataframes": dataframes,
                "pd": pd,
                "datetime": datetime
            }
            exec(code_modified, {}, local_vars)

        output = output_buffer.getvalue().strip()
        return output if output else "Execution completed with no output."

    except Exception as e:
        return f"An error occurred during code execution: {e}"

# -------------------------------------------------------------------
# Final LLM: The Agent Summation + Source Decision
# -------------------------------------------------------------------
def final_answer_llm(user_question, index_dict, python_dict):
    """
    We feed the final LLM both sets of data, but only the text 
    (the top_k from index, and the execution result from python).
    Then we let it produce a short answer that ends with 
    `Source: <some label>` or "No information was found..."

    We'll *post-process* that label to attach the code or top_k.
    """
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    # If both are empty
    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        return "No information was found in the Data. Can I help you with anything else?"

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    # This is what the final LLM sees:
    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"

    system_prompt = f"""
You are a helpful assistant. The user asked a question, and you have two possible data sources:
1) Index data: (INDEX_DATA)
2) Python data: (PYTHON_DATA)

Use only the data from those two sources to answer the question. Then decide which source(s) was used:
- "Source: Index" (if only index data is relevant),
- "Source: Python" (if only python data is relevant),
- "Source: Index & Python" (if both data pieces are used),
- Or if no data truly answers the question, "No information was found in the Data. Can I help you with anything else?"

**Important**:
- If you do find relevant info in the index text, you must say "Index" is used.
- If you do find relevant info (like a numeric answer) from the python data, you must say "Python" is used.
- If both help, say "Index & Python".
- At the end of your final answer, put "Source: X" on a new line EXACTLY.

User question:
{user_question}

INDEX_DATA:
{index_top_k}

PYTHON_DATA:
{python_result}

Chat_history:
{chat_history}
"""

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 1000,
        "temperature": 0.0,
        "stream": True
    }

    final_text = ""
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
                                final_text += content_piece
                        except json.JSONDecodeError:
                            pass
    except:
        final_text = "An error occurred while processing your request."

    final_text = final_text.strip()
    if not final_text:
        return "No information was found in the Data. Can I help you with anything else?"

    return final_text

# -------------------------------------------------------------------
# POST-PROCESS: Attach code or top_k if needed
# -------------------------------------------------------------------
def post_process_source(final_text, index_dict, python_dict):
    """
    If final_text ends with or contains "Source: Index", "Source: Python", 
    or "Source: Index & Python", we attach the code or top_k accordingly.
    """
    # We unify to lower for detection
    text_lower = final_text.lower()

    # Attempt to parse the final line with "Source:"
    # We'll do a simple check:
    if "source: index & python" in text_lower:
        # Then append the relevant pieces
        top_k_text = index_dict.get("top_k", "No information")
        code_text = python_dict.get("code", "")
        return f"""{final_text}

The Files:
{top_k_text}

The code:
{code_text}
"""
    elif "source: python" in text_lower:
        code_text = python_dict.get("code", "")
        return f"""{final_text}

The code:
{code_text}
"""
    elif "source: index" in text_lower:
        top_k_text = index_dict.get("top_k", "No information")
        return f"""{final_text}

The Files:
{top_k_text}
"""
    else:
        # No recognized source or it's "No information" fallback
        return final_text

# -------------------------------------------------------------------
# Agent
# -------------------------------------------------------------------
def agent_answer(user_question):
    # greet or empty?
    if not user_question.strip() and not chat_history:
        return "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"

    greet_list = ["hello", "hi ", "hey ", "good morning", "good evening", "assalam"]
    if any(g in user_question.lower() for g in greet_list):
        return "Hello! How may I assist you?"

    # get data
    index_dict = tool_1_index_search(user_question)
    python_dict = tool_2_code_run(user_question)

    # final llm merges
    final_ans = final_answer_llm(user_question, index_dict, python_dict)

    # post-process to attach code or files
    final_ans_with_src = post_process_source(final_ans, index_dict, python_dict)
    return final_ans_with_src

# -------------------------------------------------------------------
# Ask_Question
# -------------------------------------------------------------------
def Ask_Question(question):
    global chat_history

    chat_history.append(f"User: {question}")

    # keep short
    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2

    # run
    answer = agent_answer(question)

    chat_history.append(f"Assistant: {answer}")
    chat_history = chat_history[-max_entries:]

    # logging
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
        answer.replace('"','""'),
        "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))

    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    return answer
