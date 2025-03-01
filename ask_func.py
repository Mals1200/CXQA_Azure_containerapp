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

# Import the new PPT_Agent function
from PPT_Agent import generate_ppt

logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []

# We add this flag to track if user typed "export_ppt"
waiting_for_ppt_instructions = False

# -------------------------------------------------------------------------
# Fixed-coded tables info
# -------------------------------------------------------------------------
TABLES =  """
1) "Al-Bujairy Terrace Footfalls.xlsx", with the following tables:
   -Date: datetime64[ns], Footfalls: int64
2) "Al-Turaif Footfalls.xlsx", with the following tables:
   -Date: datetime64[ns], Footfalls: int64
3) "Complaints.xlsx", with the following tables:
   -Created On: datetime64[ns], Incident Category: object, Status: object, Resolved On Date(Local): object, Incident Description: object, Resolution: object
4) "Duty manager log.xlsx", with the following tables:
   -DM NAME: object, Date: datetime64[ns], Shift: object, Issue: object, Department: object, Team: object, Incident: object, Remark: object, Status: object, ETA: object, Days: float64
5) "Food and Beverages (F&b) Sales.xlsx", with the following tables:
   -Restaurant name: object, Category: object, Date: datetime64[ns], Covers: float64, Gross Sales: float64
6) "Meta-Data.xlsx", with the following tables:
   -Visitation: object, Attendance: object, Visitors: object, Guests: object, Footfalls: object, Unnamed: 5: object
7) "PE Observations.xlsx", with the following tables:
   -Unnamed: 0: object, Unnamed: 1: object
8) "Parking.xlsx", with the following tables:
   -Date: datetime64[ns], Valet Volume: int64, Valet Revenue: int64, Valet Utilization: float64, BCP Revenue: object, BCP Volume: int64, BCP Utilization: float64, SCP Volume: int64, SCP Revenue: int64, SCP Utilization: float64
9) "Qualitative Comments.xlsx", with the following tables:
   -Open Ended: object
10) "Tenants Violations.xlsx", with the following tables:
   -Unnamed: 0: object, Unnamed: 1: object
11) "Tickets.xlsx", with the following tables:
   -Date: datetime64[ns], Number of tickets: int64, revenue: int64, attendnace: int64, Reservation Attendnace: int64, Pass Attendance: int64, Male attendance: int64, Female attendance: int64, Rebate value: float64, AM Tickets: int64, PM Tickets: int64, Free tickets: int64, Paid tickets: int64, Free tickets %: float64, Paid tickets %: float64, AM Tickets %: float64, PM Tickets %: float64, Rebate Rate V 55: float64, Revenue  v2: int64
12) "Top2Box Summary.xlsx", with the following tables:
   -Month: datetime64[ns], Type: object, Top2Box scores/ rating: float64
13) "Total Landscape areas and quantities.xlsx", with the following tables:
   -Assets: object, Unnamed: 1: object, Unnamed: 2: object, Unnamed: 3: object
"""
SAMPLE_TEXT = """
Al-Bujairy Terrace Footfalls.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Footfalls': 2950}, ...]
Al-Turaif Footfalls.xlsx: [{'Date': "Timestamp('2023-06-01 00:00:00')", 'Footfalls': 694}, ...]
Complaints.xlsx: [{'Created On': "Timestamp('2024-01-01 00:00:00')", 'Incident Category': 'Contact Center Operation', ...}, ...]
Duty manager log.xlsx: [{'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", ...}, ...]
Food and Beverages (F&b) Sales.xlsx: [{'Restaurant name': 'Angelina', 'Category': 'Casual Dining', ...}, ...]
Meta-Data.xlsx: [{'Visitation': 'Revenue', 'Attendance': 'Income', 'Visitors': 'Sales', 'Guests': 'Gross Sales', ...}, ...]
PE Observations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...]
Parking.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Valet Volume': 194, ...}, ...]
Qualitative Comments.xlsx: [{'Open Ended': 'يفوقو توقعاتي كل شيء رائع'}, ...]
Tenants Violations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...]
Tickets.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Number of tickets': 4644, 'revenue': 288050, ...}, ...]
Top2Box Summary.xlsx: [{'Month': "Timestamp('2024-01-01 00:00:00')", 'Type': 'Bujairi Terrace/ Diriyah  offering', ...}, ...]
Total Landscape areas and quantities.xlsx: [{'Assets': 'SN', 'Unnamed: 1': 'Location', ...}, ...]
"""
SCHEMA_TEXT = """
Al-Bujairy Terrace Footfalls.xlsx: {'Date': 'datetime64[ns]', 'Footfalls': 'int64'},
Al-Turaif Footfalls.xlsx: {'Date': 'datetime64[ns]', 'Footfalls': 'int64'},
Complaints.xlsx: {'Created On': 'datetime64[ns]', 'Incident Category': 'object', 'Status': 'object', ...},
Duty manager log.xlsx: {'DM NAME': 'object', 'Date': 'datetime64[ns]', 'Shift': 'object', ...},
Food and Beverages (F&b) Sales.xlsx: {'Restaurant name': 'object', 'Category': 'object', 'Date': 'datetime64[ns]', ...},
Meta-Data.xlsx: {'Visitation': 'object', 'Attendance': 'object', 'Visitors': 'object', 'Guests': 'object', 'Footfalls': 'object', ...},
PE Observations.xlsx: {'Unnamed: 0': 'object', 'Unnamed: 1': 'object'},
Parking.xlsx: {'Date': 'datetime64[ns]', 'Valet Volume': 'int64', 'Valet Revenue': 'int64', ...},
Qualitative Comments.xlsx: {'Open Ended': 'object'},
Tenants Violations.xlsx: {'Unnamed: 0': 'object', 'Unnamed: 1': 'object'},
Tickets.xlsx: {'Date': 'datetime64[ns]', 'Number of tickets': 'int64', 'revenue': 'int64', ...},
Top2Box Summary.xlsx: {'Month': 'datetime64[ns]', 'Type': 'object', 'Top2Box scores/ rating': 'float64'},
Total Landscape areas and quantities.xlsx: {'Assets': 'object', 'Unnamed: 1': 'object', 'Unnamed: 2': 'object', 'Unnamed: 3': 'object'},
"""

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

def split_question_into_subquestions(user_question):
    text = re.sub(r"\s+and\s+", " ~SPLIT~ ", user_question, flags=re.IGNORECASE)
    text = re.sub(r"\s*&\s*", " ~SPLIT~ ", text)
    parts = text.split("~SPLIT~")
    subqs = [p.strip() for p in parts if p.strip()]
    return subqs

def is_text_relevant(question, snippet):
    if not snippet.strip():
        return False

    LLM_ENDPOINT = (
        "https://FAKE-RESOURCE-NAME.openai.azure.com/"
        "openai/deployments/FAKE-DEPLOYMENT/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_KEY"

    system_prompt = (
        "You are a classifier. We have a user question and a snippet of text. "
        "Decide if the snippet is truly relevant to answering the question. "
        "Return ONLY 'YES' or 'NO'."
    )
    user_prompt = f"Question: {question}\nSnippet: {snippet}\nRelevant? Return 'YES' or 'NO' only."

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 10,
        "temperature": 0.0,
        "stream": False
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip().upper()
        return content.startswith("YES")
    except:
        return False

def references_tabular_data(question, tables_text):
    llm_system_message = (
        "You are a helpful agent. Decide if the user's question references or requires the tabular data.\n"
        "Return ONLY 'YES' or 'NO' (in all caps)."
    )
    llm_user_message = f"""
    User question: {question}

    We have these tables: {tables_text}

    Does the user need the data from these tables to answer their question?
    Return ONLY 'YES' if it does, or ONLY 'NO' if it does not.
    """

    payload = {
        "messages": [
            {"role": "system", "content": llm_system_message},
            {"role": "user", "content": llm_user_message}
        ],
        "max_tokens": 50,
        "temperature": 0.0,
        "stream": True
    }

    response_text = stream_azure_chat_completion(
        endpoint="https://FAKE-RESOURCE-NAME.openai.azure.com/openai/deployments/FAKE-DEPLOYMENT/chat/completions?api-version=2024-08-01-preview",
        headers={
            "Content-Type": "application/json",
            "api-key": "FAKE_KEY"
        },
        payload=payload,
        print_stream=False
    )

    clean_response = response_text.strip().upper()
    return "YES" in clean_response

def tool_1_index_search(user_question, top_k=5):
    SEARCH_SERVICE_NAME = "FAKE-SEARCH-SVC"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    INDEX_NAME = "FAKE-INDEX-NAME"
    ADMIN_API_KEY = "FAKE_ADMIN_KEY"

    subquestions = split_question_into_subquestions(user_question)

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

        relevant_texts = []
        for r in results:
            snippet = r.get("content", "").strip()
            keep_snippet = False
            for sq in subquestions:
                if is_text_relevant(sq, snippet):
                    keep_snippet = True
                    break

            if keep_snippet:
                relevant_texts.append(snippet)

        if not relevant_texts:
            return {"top_k": "No information"}

        combined = "\n\n---\n\n".join(relevant_texts)
        return {"top_k": combined}

    except Exception as e:
        return {"top_k": f"Error in Tool1 (Index Search): {str(e)}"}

def tool_2_code_run(user_question):
    # Decide if question references tabular data
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    LLM_ENDPOINT = (
        "https://FAKE-RESOURCE-NAME.openai.azure.com/"
        "openai/deployments/FAKE-DEPLOYMENT/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_KEY"

    system_prompt = f"""
You are a python expert. Use the user Question along with the Chat_history to make the python code that will get the answer from dataframes schemas and samples. 
Only provide the python code, and nothing else, stripped of any triple backticks.
Take aggregation/analysis step by step and always double check correct columns/values. 
Don't give examples, only provide actual code. If you can't provide the code, say "404".

**Rules**:
1. Only use tables/columns that actually exist, do not fabricate columns.
2. Do not rely on sample rows as complete data. There may be more data.
3. Return pure Python code that can be executed directly, with imports if needed.
4. The code must print the final answer.

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

        execution_result = execute_generated_code(code_str)
        return {"result": execution_result, "code": code_str}

    except Exception as ex:
        return {
            "result": f"Error in Tool2 (Code Generation/Execution): {str(ex)}",
            "code": ""
        }

def execute_generated_code(code_str):
    account_url = "https://FAKE-BLOBACCOUNT.blob.core.windows.net"
    sas_token = "FAKE_SAS_TOKEN"
    container_name = "FAKE-CONTAINER"
    target_folder_path = "UI/FAKE/cxqa_data/tabular/"

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
                df = pd.read_excel(BytesIO(blob_data))
            elif file_name.endswith('.csv'):
                df = pd.read_csv(BytesIO(blob_data))
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

def tool_3_llm_fallback(user_question):
    LLM_ENDPOINT = (
        "https://FAKE-RESOURCE-NAME.openai.azure.com/"
        "openai/deployments/FAKE-DEPLOYMENT/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_KEY"

    system_prompt = (
        "You are a highly knowledgeable large language model. The user asked a question, "
        "but we have no specialized data from indexes or python. Provide a concise answer using your general knowledge."
    )

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 500,
        "temperature": 0.7,
        "stream": True
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    fallback_answer = ""
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
                                fallback_answer += content_piece
                        except json.JSONDecodeError:
                            pass
    except:
        fallback_answer = "I'm sorry, but I couldn't retrieve a fallback answer."

    return fallback_answer.strip()

def final_answer_llm(user_question, index_dict, python_dict):
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        return f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"

    LLM_ENDPOINT = (
        "https://FAKE-RESOURCE-NAME.openai.azure.com/"
        "openai/deployments/FAKE-DEPLOYMENT/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_KEY"

    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"

    system_prompt = f"""
You are a helpful assistant. The user asked a question, and you have two data sources:
1) Index data: (INDEX_DATA)
2) Python data: (PYTHON_DATA)

Use only these two sources to answer. 
At the end of the final answer, put EXACTLY one line with "Source: X" 
where X can be:
- "Index" if only index data was used,
- "Python" if only python data was used,
- "Index & Python" if both were used,
- or "No information was found in the Data. Can I help you with anything else?" if none is truly relevant.

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

def post_process_source(final_text, index_dict, python_dict):
    text_lower = final_text.lower()

    if "source: index & python" in text_lower:
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
        return final_text

def agent_answer(user_question):
    """
    The main logic to handle user question, including 'export_ppt' flow.
    """
    # If question is empty at first usage
    if user_question.strip() == "" and len(chat_history) < 2:
        return ""

    # Check if entire user input is basically a greeting
    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "good", "morning", "evening",
            "assalam", "hayo", "hola", "salam", "alsalam",
            "alsalamualaikum", "al", "salam"
        }
        tokens = re.findall(r"[A-Za-z]+", phrase.lower())
        if not tokens:
            return False
        for t in tokens:
            if t not in greet_words:
                return False
        return True

    global waiting_for_ppt_instructions
    user_question_stripped = user_question.strip()

    # 1) If we are waiting for PPT instructions, this new user message is the instructions
    if waiting_for_ppt_instructions:
        waiting_for_ppt_instructions = False  # reset the flag

        # The last user question & answer from chat_history
        latest_user_q = ""
        latest_answer = ""
        for entry in reversed(chat_history):
            if entry.startswith("User:"):
                latest_user_q = entry.replace("User:", "").strip()
                break
        for entry in reversed(chat_history):
            if entry.startswith("Assistant:"):
                latest_answer = entry.replace("Assistant:", "").strip()
                break

        instructions = user_question_stripped

        # Call the PPT_Agent function with all needed arguments
        ppt_link = generate_ppt(
            latest_question=latest_user_q,
            latest_answer=latest_answer,
            chat_history=chat_history,
            instructions=instructions
        )
        return f"Here is your PPT link:\n{ppt_link}"

    # 2) If entire phrase is basically a greeting
    if is_entirely_greeting_or_punc(user_question_stripped):
        if len(chat_history) < 4:
            return "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"
        else:
            return "Hello! How may I assist you?"

    # 3) If user typed "export_ppt", ask for PPT instructions
    if user_question_stripped.lower() == "export_ppt":
        waiting_for_ppt_instructions = True
        return "Sure! Please provide the instructions or details for the PPT."

    # 4) Otherwise, normal Q&A logic
    index_dict = tool_1_index_search(user_question_stripped)
    python_dict = tool_2_code_run(user_question_stripped)
    final_ans = final_answer_llm(user_question_stripped, index_dict, python_dict)
    final_ans_with_src = post_process_source(final_ans, index_dict, python_dict)
    return final_ans_with_src

def Ask_Question(question):
    global chat_history

    chat_history.append(f"User: {question}")

    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2

    answer = agent_answer(question)

    chat_history.append(f"Assistant: {answer}")
    chat_history = chat_history[-max_entries:]

    # Logging
    account_url = "https://FAKE-BLOBACCOUNT.blob.core.windows.net"
    sas_token = "FAKE_SAS_TOKEN"
    container_name = "FAKE-CONTAINER"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)

    target_folder_path = "UI/FAKE/cxqa_data/logs/"
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
