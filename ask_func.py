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
from tenacity import retry, stop_after_attempt, wait_fixed  # retrying
from functools import lru_cache  # caching
import re
import difflib

def clean_repeated_patterns(text):
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)
    return text.strip()

def clean_repeated_phrases(text):
    # Additional call for repeated phrases
    return re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)

tool_cache = {}
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []

TABLES = """
1) "Al-Bujairy Terrace Footfalls.xlsx", columns: Date(datetime64[ns]), Footfalls(int64)
2) "Al-Turaif Footfalls.xlsx", columns: Date(datetime64[ns]), Footfalls(int64)
3) "Complaints.xlsx", columns: Created On(datetime64[ns]), Incident Category(object), ...
4) "Duty manager log.xlsx", ...
5) "Food and Beverages (F&b) Sales.xlsx", ...
6) "Meta-Data.xlsx", ...
7) "PE Observations.xlsx", ...
8) "Parking.xlsx", ...
9) "Qualitative Comments.xlsx", ...
10) "Tenants Violations.xlsx", ...
11) "Tickets.xlsx", ...
12) "Top2Box Summary.xlsx", ...
13) "Total Landscape areas and quantities.xlsx", ...
"""

SAMPLE_TEXT = "(omitted for brevity)"
SCHEMA_TEXT = "(omitted for brevity)"

def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    """
    A simple function for one-shot completions with Azure OpenAI.
    """
    response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "choices" in data and data["choices"]:
        return data["choices"][0]["message"]["content"]
    return ""

def split_question_into_subquestions(user_question):
    """
    Break question into sub-questions. If none, return single item list.
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = """
    You are an expert in semantic parsing.
    Split only if multiple distinct sub-questions exist.
    Return a valid JSON array of strings.
    Otherwise return the original question in a single-element array.
    """
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Question: {user_question}\nReturn JSON array only."}
        ],
        "max_tokens": 500,
        "temperature": 0
    }
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}
    try:
        resp = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        arr = json.loads(content)
        if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
            return arr
        return [user_question]
    except:
        return [user_question]

def is_text_relevant(question, snippet):
    """
    Quick yes/no classification if snippet is relevant to question.
    """
    if not snippet.strip():
        return False

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = "You are a classifier. Return ONLY 'YES' or 'NO'."
    user_prompt = f"Question: {question}\nSnippet: {snippet}\nIs snippet relevant? Yes or No only."

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 10,
        "temperature": 0
    }
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}
    try:
        r = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        decision = r.json()["choices"][0]["message"]["content"].strip().upper()
        return decision.startswith("YES")
    except:
        return False

def references_tabular_data(question, tables_text):
    """
    Decides if user question needs the data from the provided tables.
    We'll do a quick keyword fallback for "footfall, visits, tickets," etc.
    Then we do the LLM check if needed.
    """
    # Simple fallback for synonyms
    question_lower = question.lower()
    keywords = [
        "footfall", "footfalls", "visits", "attendance", "tickets",
        "parking", "complaints", "sales", "f&b", "beverage", 
        "revenue", "bujairy", "albujairy", "turaif", "dm log",
        "incident", "resolved on date"
    ]
    for kw in keywords:
        if kw in question_lower:
            return True  # We consider it needing data

    # If no direct keyword, we do an LLM-based check
    llm_prompt = f"""
You are a strict YES/NO classifier. 
Question: {question}
Available Tables: {tables_text}
Decide if question requires data from these tables. 
Reply ONLY 'YES' or 'NO'.
    """
    payload = {
        "messages": [
            {"role": "system", "content": "Decide if user needs tabular data. Return YES or NO only."},
            {"role": "user", "content": llm_prompt}
        ],
        "max_tokens": 5,
        "temperature": 0
    }
    try:
        result = stream_azure_chat_completion(
            endpoint="https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview",
            headers={"Content-Type": "application/json", "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"},
            payload=payload
        )
        return "YES" in result.strip().upper()
    except:
        return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    """
    Retrieve top_k relevant text snippets from Azure Cognitive Search.
    """
    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    INDEX_NAME = "cxqa-ind-v6"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

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
            # Check if snippet relevant to any subquestion
            keep_it = any(is_text_relevant(sq, snippet) for sq in subquestions)
            if keep_it:
                relevant_texts.append(snippet)

        if not relevant_texts:
            return {"top_k": "No information"}
        combined = "\n\n---\n\n".join(relevant_texts)
        return {"top_k": combined}
    except Exception as e:
        logging.error(f"Error in tool_1_index_search: {str(e)}")
        return {"top_k": "No information"}

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    """
    If references_tabular_data => generate Python code from LLM => run it => return code output & code text.
    If not needed => "No information"
    """
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = f"""
You are a Python data expert. 
User question: {user_question}
We have dataframes from the files described below. 
Generate Python code that uses these dataframes to answer the question. 
Return a print(...) with the final answer. No extra explanation.
If you cannot produce the code, return '404'.

Schemas:
{SCHEMA_TEXT}

Samples:
{SAMPLE_TEXT}

Chat history:
{chat_history}

Notes:
- "Footfalls" means visits or attendance in Al-Bujairy or Al-Turaif. 
- Provide the final aggregated or filtered answer in a print statement.
"""
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 1200,
        "temperature": 0.7
    }
    try:
        code_str = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        code_str = code_str.strip()
        if not code_str or code_str == "404":
            return {"result": "No information", "code": ""}

        # run code
        execution_result = execute_generated_code(code_str)
        return {"result": execution_result, "code": code_str}
    except Exception as ex:
        logging.error(f"Error in tool_2_code_run: {ex}")
        return {"result": "No information", "code": ""}

def execute_generated_code(code_str):
    """
    Load the real data from your blob, then run the user's code with dataframes variable.
    Replace read_excel/read_csv calls with dataframes.get(...) 
    """
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
            fname = blob.name.split('/')[-1]
            bc = container_client.get_blob_client(blob.name)
            blob_data = bc.download_blob().readall()

            if fname.endswith(".xlsx") or fname.endswith(".xls"):
                df = pd.read_excel(io.BytesIO(blob_data))
            elif fname.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(blob_data))
            else:
                continue
            dataframes[fname] = df

        # Replace read calls
        code_modified = code_str.replace("pd.read_excel(", "dataframes.get(")
        code_modified = code_modified.replace("pd.read_csv(", "dataframes.get(")

        output_buf = StringIO()
        with contextlib.redirect_stdout(output_buf):
            local_vars = {
                "dataframes": dataframes,
                "pd": pd,
                "datetime": datetime
            }
            exec(code_modified, {}, local_vars)

        output = output_buf.getvalue().strip()
        return output if output else "Execution completed with no output."
    except Exception as e:
        return f"An error occurred: {e}"

def tool_3_llm_fallback(user_question):
    """
    If neither index nor python has data, answer from general knowledge
    (or return short statement).
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = "You are a general-knowledge assistant. Provide a short direct answer if possible."
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 200,
        "temperature": 0.7
    }
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}
    try:
        fallback_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        return fallback_answer.strip()
    except:
        return "I'm sorry, I'm unable to provide more details right now."

def final_answer_llm(user_question, index_dict, python_dict):
    """
    Combine index + python data in a final LLM call. 
    If both are "No info", we do fallback. 
    """
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    # If no info from either => fallback
    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback = tool_3_llm_fallback(user_question)
        return f"{fallback}\nSource: Ai Generated"

    # Otherwise we pass both to the final LLM
    LLM_ENDPOINT = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = f"""
You have 2 data sources:
INDEX_DATA:
{index_top_k}

PYTHON_DATA:
{python_result}

User question: {user_question}

Rules:
- Use or quote from them if relevant. 
- At the end, put exactly one line: "Source: X"
  where X can be:
    - "Index" if only index data was used,
    - "Python" if only python data was used,
    - "Index & Python" if both were used,
    - "No information was found in the Data." if you didn't actually use them.

Reply concisely.
"""
    user_prompt = user_question
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 800,
        "temperature": 0
    }
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}
    try:
        result = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        return result.strip()
    except:
        return "An error occurred while generating the final answer."

def post_process_source(final_text, index_dict, python_dict):
    """
    If final_text ends with e.g. "Source: Index & Python", 
    we append the top_k snippet or the code in hidden form. 
    We'll store them in some bracket that the Bot can show/hide.
    """
    txt_lower = final_text.lower()

    # Prepare separate lines for the source details
    to_append = []
    if "source: index & python" in txt_lower:
        # Show both
        idx_snip = index_dict.get("top_k","No info").strip()
        code_str = python_dict.get("code","")
        if idx_snip != "No information":
            to_append.append("\n\n[INDEX RESULTS]\n" + idx_snip)
        if code_str:
            to_append.append("\n[PYTHON CODE]\n" + code_str)
    elif "source: index" in txt_lower:
        idx_snip = index_dict.get("top_k","No info").strip()
        if idx_snip != "No information":
            to_append.append("\n\n[INDEX RESULTS]\n" + idx_snip)
    elif "source: python" in txt_lower:
        code_str = python_dict.get("code","")
        if code_str:
            to_append.append("\n\n[PYTHON CODE]\n" + code_str)

    if not to_append:
        return final_text
    else:
        # We'll append them after some delimiter
        final_text += "\n\n---SOURCE_DETAILS---\n" + "\n".join(to_append)
        return final_text

def agent_answer(user_question):
    """
    Orchestrate the final answer. 
    - Possibly get python data
    - get index data
    - final LLM
    - attach source if relevant
    """
    def is_entirely_greeting(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening",
            "assalam", "salam", "hola", "greetings", "howdy", "yo"
        }
        tokens = re.findall(r"[a-z]+", phrase.lower())
        if not tokens:
            return False
        for t in tokens:
            if t not in greet_words:
                return False
        return True

    q_stripped = user_question.strip()
    if is_entirely_greeting(q_stripped):
        if len(chat_history) < 4:
            return (
                "Hello! I'm The CXQA AI Assistant. I'm here to help. "
                "What would you like to know today?\n"
                "To reset the conversation, type 'restart chat'.\n"
                "To generate Slides, Charts, or Document, type 'export ...'"
            )
        else:
            return (
                "Hello! How may I assist you?\n"
                "To reset, type 'restart chat'.\n"
                "To generate Slides, Charts, or Document, type 'export ...'"
            )

    # Check if we have a cached answer
    cache_key = q_stripped.lower()
    if cache_key in tool_cache:
        return tool_cache[cache_key][2]

    # run python data
    python_dict = tool_2_code_run(user_question)
    # run index
    index_dict = tool_1_index_search(user_question)

    final_text = final_answer_llm(user_question, index_dict, python_dict)
    final_text = clean_repeated_phrases(final_text)

    # attach top_k or code if "Source" calls for it
    final_answer = post_process_source(final_text, index_dict, python_dict)

    tool_cache[cache_key] = (index_dict, python_dict, final_answer)
    return final_answer

def Ask_Question(question):
    """
    Top-level function: 
    - If "export", do export
    - If "restart chat", clear
    - else normal answer
    """
    global chat_history
    q_lower = question.lower().strip()

    if q_lower.startswith("export"):
        from Export_Agent import Call_Export
        if len(chat_history) < 2:
            yield "Error: Not enough conversation history to perform export."
            return
        instructions = question[6:].strip()
        latest_question = chat_history[-1]
        latest_answer = chat_history[-2]
        gen = Call_Export(latest_question, latest_answer, chat_history, instructions)
        combined = ''.join(gen)
        yield combined
        return

    if q_lower == "restart chat":
        chat_history = []
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    answer_text = agent_answer(question)

    if "Hello!" in answer_text or "How may I assist you?" in answer_text:
        yield answer_text  # Send greeting response
        return
    
    
    # normal Q&A
    chat_history.append(f"User: {question}")
    chat_history.append(f"Assistant: {answer_text}")

    if len(chat_history) > 12:
        chat_history = chat_history[-12:]

    # Logging
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

    yield answer_text
