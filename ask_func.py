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
    # Remove repeated words like: "TheThe", "total total"
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)
    # Remove repeated characters within a word: e.g., "footfallsfalls"
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', text, flags=re.IGNORECASE)
    # Remove excessive punctuation or spaces
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)
    return text.strip()

def clean_repeated_phrases(text):
    """Removes repeated words like 'TheThe' or 'total total'."""
    return re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)

def is_repeated_phrase(last_text, new_text, threshold=0.98):
    """
    Detect if new_text is highly similar to the end of last_text.
    """
    if not last_text or not new_text:
        return False
    comparison_length = min(len(last_text), 100)
    recent_text = last_text[-comparison_length:]
    similarity = difflib.SequenceMatcher(None, recent_text, new_text).ratio()
    return similarity > threshold

def deduplicate_streaming_tokens(last_tokens, new_token):
    if last_tokens.endswith(new_token):
        return ""
    return new_token

tool_cache = {}
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []

# -------------------------------------------------------------------------
# Fixed coded tables info for decision. and schema/sample for writing code
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
Al-Bujairy Terrace Footfalls.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Footfalls': 2950}, ...],
Al-Turaif Footfalls.xlsx: [{'Date': "Timestamp('2023-06-01 00:00:00')", 'Footfalls': 694}, ...],
Complaints.xlsx: [{'Created On': "Timestamp('2024-01-01 00:00:00')", 'Incident Category': 'Contact Center Operation', ...}],
Duty manager log.xlsx: [{'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", ...}],
Food and Beverages (F&b) Sales.xlsx: [{'Restaurant name': 'Angelina', 'Category': 'Casual Dining', ...}],
Meta-Data.xlsx: [{'Visitation': 'Revenue', 'Attendance': 'Income', ...}],
PE Observations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...],
Parking.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Valet Volume': 194, 'Valet Revenue': 29100, ...}],
Qualitative Comments.xlsx: [{'Open Ended': 'يفوقو توقعاتي كل شيء رائع'}, ...],
Tenants Violations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...],
Tickets.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Number of tickets': 4644, 'revenue': 288050, ...}],
Top2Box Summary.xlsx: [{'Month': "Timestamp('2024-01-01 00:00:00')", 'Type': 'Bujairi Terrace/ Diriyah  offering', ...}],
Total Landscape areas and quantities.xlsx: [{'Assets': 'SN', 'Unnamed: 1': 'Location', ...}],
"""
SCHEMA_TEXT = """
Al-Bujairy Terrace Footfalls.xlsx: {'Date': 'datetime64[ns]', 'Footfalls': 'int64'},
Al-Turaif Footfalls.xlsx: {'Date': 'datetime64[ns]', 'Footfalls': 'int64'},
Complaints.xlsx: {'Created On': 'datetime64[ns]', 'Incident Category': 'object', 'Status': 'object', 'Resolved On Date(Local)': 'object', 'Incident Description': 'object', 'Resolution': 'object'},
Duty manager log.xlsx: {'DM NAME': 'object', 'Date': 'datetime64[ns]', 'Shift': 'object', 'Issue': 'object', 'Department': 'object', 'Team': 'object', 'Incident': 'object', 'Remark': 'object', 'Status': 'object', 'ETA': 'object', 'Days': 'float64'},
Food and Beverages (F&b) Sales.xlsx: {'Restaurant name': 'object', 'Category': 'object', 'Date': 'datetime64[ns]', 'Covers': 'float64', 'Gross Sales': 'float64'},
Meta-Data.xlsx: {'Visitation': 'object', 'Attendance': 'object', 'Visitors': 'object', 'Guests': 'object', 'Footfalls': 'object', 'Unnamed: 5': 'object'},
PE Observations.xlsx: {'Unnamed: 0': 'object', 'Unnamed: 1': 'object'},
Parking.xlsx: {'Date': 'datetime64[ns]', 'Valet Volume': 'int64', 'Valet Revenue': 'int64', 'Valet Utilization': 'float64', 'BCP Revenue': 'object', 'BCP Volume': 'int64', 'BCP Utilization': 'float64', 'SCP Volume': 'int64', 'SCP Revenue': 'int64', 'SCP Utilization': 'float64'},
Qualitative Comments.xlsx: {'Open Ended': 'object'},
Tenants Violations.xlsx: {'Unnamed: 0': 'object', 'Unnamed: 1': 'object'},
Tickets.xlsx: {'Date': 'datetime64[ns]', 'Number of tickets': 'int64', 'revenue': 'int64', 'attendnace': 'int64', 'Reservation Attendnace': 'int64', 'Pass Attendance': 'int64', 'Male attendance': 'int64', 'Female attendance': 'int64', 'Rebate value': 'float64', 'AM Tickets': 'int64', 'PM Tickets': 'int64', 'Free tickets': 'int64', 'Paid tickets': 'int64', 'Free tickets %': 'float64', 'Paid tickets %': 'float64', 'AM Tickets %': 'float64', 'PM Tickets %': 'float64', 'Rebate Rate V 55': 'float64', 'Revenue  v2': 'int64'},
Top2Box Summary.xlsx: {'Month': 'datetime64[ns]', 'Type': 'object', 'Top2Box scores/ rating': 'float64'},
Total Landscape areas and quantities.xlsx: {'Assets': 'object', 'Unnamed: 1': 'object', 'Unnamed: 2': 'object', 'Unnamed: 3': 'object'},
"""

# -------------------------------------------------------------------
# LLM streaming and simpler “one-shot” fetch from Azure
# -------------------------------------------------------------------
def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    """
    This function can be used for streaming from Azure, 
    but we only do batch accumulate in these scripts.
    """
    final_text = ""
    response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "choices" in data and data["choices"]:
        final_text = data["choices"][0]["message"]["content"]
    return final_text

def split_question_into_subquestions(user_question):
    """
    Determine if the question should be split into multiple sub-questions.
    Returns a list of sub-questions if needed, otherwise returns single item list.
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = """
    You are an expert in semantic parsing. Your task is to carefully split complex questions into their most meaningful sub-questions.
    
    **Rules:**
    1. Split **only** if the question has distinct, meaningful sub-questions.
    2. Return a **valid JSON array of strings**. 
    3. If no splitting is necessary, return the original question as a single-item list.
    """

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Question: {user_question}\n\nReturn the JSON array of sub-questions."
            }
        ],
        "max_tokens": 500,
        "temperature": 0,
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()

        if "choices" in result and result["choices"] and "message" in result["choices"][0]:
            content = result["choices"][0]["message"]["content"].strip()
        else:
            return [user_question]

        subquestions = json.loads(content)
        if isinstance(subquestions, list) and all(isinstance(q, str) for q in subquestions):
            return subquestions
        else:
            return [user_question]
    except:
        return [user_question]


def is_text_relevant(question, snippet):
    """
    Quick yes/no classification to see if snippet is relevant to question.
    """
    if not snippet.strip():
        return False

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

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
        "temperature": 0.0
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
    """
    Decide if the user's question requires using the tabular data
    (returns True/False).
    """
    llm_system_message = (
        "You are a strict YES/NO classifier. "
        "Does the user question require the available tabular data to answer?\n"
        "Reply ONLY 'YES' or 'NO'."
    )
    llm_user_message = f"""
    User Question:
    {question}

    Available Tables:
    {tables_text}

    Decision rules:
    1. Reply 'YES' if question needs facts/statistics from these tables.
    2. Reply 'NO' if it's more general, doesn't need real data from these tables.
    3. Only reply 'YES' or 'NO'.
    """

    payload = {
        "messages": [
            {"role": "system", "content": llm_system_message},
            {"role": "user", "content": llm_user_message}
        ],
        "max_tokens": 5,
        "temperature": 0.0
    }

    try:
        llm_response = stream_azure_chat_completion(
            endpoint="https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview",
            headers={
                "Content-Type": "application/json",
                "api-key": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"
            },
            payload=payload,
            print_stream=False
        )
        return "YES" in llm_response.strip().upper()
    except:
        return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    """
    Connect to Azure Cognitive Search to find relevant text.
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
        logging.error(f"Error in Tool1 (Index Search): {e}")
        return {"top_k": f"No information"}

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    """
    If references_tabular_data is True, we try generating Python code to query the data.
    Then run that code in memory. Return the result and the code.
    """
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = f"""
You are a python expert. Use the user Question along with the Chat_history to make python code that gets the answer from the dataframes. 
Only provide the python code (no explanation). 
Return a print statement with the final answer. If not possible, return the string "404".

User question:
{user_question}

Dataframes schemas:
{SCHEMA_TEXT}

Dataframes samples:
{SAMPLE_TEXT}

Chat_history:
{chat_history}
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
        code_str = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload, print_stream=False)
        code_str = code_str.strip()

        # If the code was "404" or empty, no info
        if not code_str or code_str == "404":
            return {"result": "No information", "code": ""}

        execution_result = execute_generated_code(code_str)
        return {"result": execution_result, "code": code_str}

    except Exception as ex:
        logging.error(f"Error in tool_2_code_run: {ex}")
        return {"result": "No information", "code": ""}

def execute_generated_code(code_str):
    """
    Safely load the XLSX/CSV from blob, rename the read calls, and run the code.
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

        # Replace read_xxx calls with dataframes dict
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
    """
    A final fallback if neither index nor python data is found. 
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = (
        "You are a highly knowledgeable model. The user asked a question, but we have no specialized data. "
        "Provide a concise, direct answer from general knowledge."
    )
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 500,
        "temperature": 0.7
    }
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}

    try:
        fallback_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        return fallback_answer.strip()
    except:
        return "I'm sorry, I'm unable to provide a fallback answer right now."

def final_answer_llm(user_question, index_dict, python_dict):
    """
    Combine index data + python data into a final LLM call 
    to produce the best short answer. 
    """
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    # If both are "No information", fallback
    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        return f"{fallback_text}\nSource: Ai Generated"

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = f"""
You have two data sources:
1) Index data (INDEX_DATA)
2) Python data (PYTHON_DATA)

Use them if relevant. Then at the END of your final answer:
Write EXACTLY one line "Source: X" 
where X can be:
 - "Index" if you only used index
 - "Python" if you only used python
 - "Index & Python" if you used both
 - or "No information was found in the Data. Can I help you with anything else?" if there's truly no data.

User question:
{user_question}

INDEX_DATA:
{index_top_k}

PYTHON_DATA:
{python_result}

chat_history:
{chat_history}
"""

    user_payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 1000,
        "temperature": 0.0
    }
    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}

    try:
        raw_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, user_payload)
        return raw_answer
    except:
        return "\n\nAn error occurred while processing your request."

def post_process_source(final_text, index_dict, python_dict):
    """
    If there's "Source: XYZ" we can optionally show more. 
    Here, we simplify to just returning the final text 
    because we handle the layout in the Bot code.
    """
    return final_text

def agent_answer(user_question):
    """
    Orchestrates the entire logic to produce a single final answer text (no partial streaming).
    """

    # Quick greeting check
    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening", "goodmorning", "good morning",
            "goodevening", "good evening", "assalam", "hola", "salam",
            "assalamualaikum", "greetings", "howdy", "what's up", "yo",
            "namaste", "shalom", "bonjour", "ciao", "konichiwa",
            "ni", "hao", "marhaba", "ahlan", "sawubona", "hallo", "salut"
        }
        tokens = re.findall(r"[A-Za-z]+", phrase.lower())
        if not tokens:
            return False
        for t in tokens:
            if t not in greet_words:
                return False
        return True

    user_question_stripped = user_question.strip()
    if is_entirely_greeting_or_punc(user_question_stripped):
        if len(chat_history) < 4:
            return ("Hello! I'm The CXQA AI Assistant. I'm here to help. "
                    "What would you like to know today?\n"
                    "- To reset the conversation, type 'restart chat'.\n"
                    "- To generate Slides, Charts, or Document, type 'export ...'")
        else:
            return ("Hello! How may I assist you?\n"
                    "- To reset, type 'restart chat'.\n"
                    "- To generate Slides, Charts, or Document, type 'export ...'")

    # Check cache
    cache_key = user_question_stripped.lower()
    if cache_key in tool_cache:
        return tool_cache[cache_key][2]  # The final answer from cache

    # Step 1: Possibly generate code if we need tabular data
    python_dict = tool_2_code_run(user_question)
    # Step 2: Always do index search
    index_dict = tool_1_index_search(user_question)

    # Step 3: Combine them
    final_text = final_answer_llm(user_question, index_dict, python_dict)
    final_text = clean_repeated_phrases(final_text)
    final_text_with_source = post_process_source(final_text, index_dict, python_dict)

    # Cache
    tool_cache[cache_key] = (index_dict, python_dict, final_text_with_source)
    return final_text_with_source

def Ask_Question(question):
    """
    The top-level function for /ask or for bot usage.
    - If question starts with 'export', we route to export logic
    - If question == 'restart chat', we clear chat
    - Otherwise, we produce an answer via agent_answer
    """
    global chat_history
    question_lower = question.lower().strip()

    # 1) Export?
    if question_lower.startswith("export"):
        from Export_Agent import Call_Export

        if len(chat_history) >= 2:
            latest_question = chat_history[-1]
            latest_answer = chat_history[-2]
        else:
            yield "Error: Not enough conversation history to perform export. Please ask at least one question first."
            return

        instructions = question[6:].strip()
        export_gen = Call_Export(
            latest_question=latest_question,
            latest_answer=latest_answer,
            chat_history=chat_history,
            instructions=instructions
        )
        output = ''.join(export_gen)
        yield output
        return

    # 2) Restart chat
    if question_lower == "restart chat":
        chat_history = []
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # Normal question
    chat_history.append(f"User: {question}")
    answer_text = agent_answer(question)
    chat_history.append(f"Assistant: {answer_text}")

    # keep chat_history short
    if len(chat_history) > 10:
        chat_history = chat_history[-10:]

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
        question.replace('"', '""'),
        answer_text.replace('"', '""'),
        "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))

    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    yield answer_text
