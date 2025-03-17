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

# For retries
from tenacity import retry, stop_after_attempt, wait_fixed

logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

#######################################################################
# In-memory cache to store repeated question answers (per enhancements)
#######################################################################
tool_cache = {}

#######################################################################
# Chat History
#######################################################################
chat_history = []

#######################################################################
# Tables / Sample text / Schema text (as provided)
#######################################################################
TABLES = """
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
Duty manager log.xlsx: [{'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", 'Shift': 'Morning Shift', ...}],
Food and Beverages (F&b) Sales.xlsx: [{'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': "Timestamp('2023-08-01 00:00:00')", ...}],
Meta-Data.xlsx: [{'Visitation': 'Revenue', 'Attendance': 'Income', 'Visitors': 'Sales', 'Guests': 'Gross Sales', 'Footfalls': nan, ...}],
PE Observations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...],
Parking.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Valet Volume': 194, 'Valet Revenue': 29100, ...}],
Qualitative Comments.xlsx: [{'Open Ended': 'يفوقو توقعاتي كل شيء رائع'}, ...],
Tenants Violations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...],
Tickets.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Number of tickets': 4644, 'revenue': 288050, ...}],
Top2Box Summary.xlsx: [{'Month': "Timestamp('2024-01-01 00:00:00')", 'Type': 'Bujairi Terrace/ Diriyah  offering', ...}],
Total Landscape areas and quantities.xlsx: [{'Assets': 'SN', 'Unnamed: 1': 'Location', 'Unnamed: 2': 'Unit', 'Unnamed: 3': 'Quantity'}, ...],
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

#########################################################################
# Split question into subquestions (basic approach, can be improved)
#########################################################################
def split_question_into_subquestions(user_question):
    
    ###############################
    #1) BASIC REGEX-BASED APPROACH
    ###############################
    # text = re.sub(r"\s+and\s+", " ~SPLIT~ ", user_question, flags=re.IGNORECASE)
    # text = re.sub(r"\s*&\s*", " ~SPLIT~ ", text)
    # parts = text.split("~SPLIT~")
    # subqs = [p.strip() for p in parts if p.strip()]
    # return subqs
    
    ###############################
    #2) SEMANTIC PARSING APPROACH
    ###############################
    """
    Uses an LLM to semantically parse and split a user question into subquestions.

    IMPORTANT:
      - Replace LLM_ENDPOINT and LLM_API_KEY with your actual Azure OpenAI deployment URL and key.
      - The LLM prompt is an example. You can modify it for your specific needs or format preferences.
      - Ensure you handle potential errors properly (e.g., missing fields in the response).
    """

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )    
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = (
        "You are a helpful assistant. "
        "You receive a user question which may have multiple parts. "
        "Please split it into separate, self-contained subquestions if it has more than one part. "
        "If it's only a single question, simply return that one. "
        "Return each subquestion on a separate line or as bullet points. "
    )

    user_prompt = f"""
    If applicable Please split the following question into distinct subquestions:\n\n{user_question}\n\n
    If not applicable just return the question as it is.
    """

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 300,
        "temperature": 0.0
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    # Send request to Azure OpenAI endpoint
    response = requests.post(LLM_ENDPOINT, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()

    # Get the text output from the LLM
    answer_text = data["choices"][0]["message"]["content"].strip()

    # EXAMPLE PARSING APPROACH:
    # Assume the LLM returns each subquestion on its own line or bullet.
    # We'll split on newlines, then strip out leading punctuation or bullet symbols.
    lines = [
        line.lstrip("•-0123456789). ").strip()
        for line in answer_text.split("\n")
        if line.strip()
    ]

    # Filter out any empty strings (just in case)
    subqs = [l for l in lines if l]

    return subqs

#########################################################################
# Simple helper to check snippet relevance (YES or NO) – improved logic
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def is_text_relevant(question, snippet):
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

#########################################################################
# Decide if user question references tabular data
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def references_tabular_data(question, tables_text, recent_history):
    """
    Improved logic: We'll do a single request, no streaming, strict yes/no.
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = (
        "You are a helpful agent. Decide if the user's question references or requires the tabular data.\n"
        "Return ONLY 'YES' or 'NO' (in all caps).\n"
        "The tables are not exclusive to the data it has, this is just a sample. **dont use the content of the sample table as the complete content. There are other rows the you were not shown**."
    )
    user_prompt = (
        f"User question: {question}\n\n"
        f"Previous conversation:\n{recent_history[-4:]}\n\n"
        f"We have these tables: {tables_text}\n\n"
        "Does the user need the data from these tables to answer their question?\n"
        "Return ONLY 'YES' if it does, or ONLY 'NO' if it does not."
    )

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 50,
        "temperature": 0.0
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    response = requests.post(LLM_ENDPOINT, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    answer_raw = data["choices"][0]["message"]["content"].strip().upper()
    return "YES" in answer_raw

#########################################################################
# Tool 1: Azure Index-based Search with semantic config + relevance check
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    """
    Searches the Azure AI Search index using semantic search and retrieves top_k results.
    This function allows switching between `cxqa-ind-v6` (old) and `vector-1741790186391-12-3-2025` (new)
    by **changing the index name, semantic configuration, and content field**.
    
    Parameters:
        - user_question (str): The query to search.
        - top_k (int): Number of top results to retrieve.

    Returns:
        - dict: A dictionary with the search results.
    """

    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    # 🔹 CHOOSE INDEX (Comment/Uncomment as needed)
    INDEX_NAME = "vector-1741865904949"  # ✅ Use new index
    # INDEX_NAME = "cxqa-ind-v6"  # ✅ Use old index

    # 🔹 CHOOSE SEMANTIC CONFIGURATION (Comment/Uncomment as needed)
    SEMANTIC_CONFIG_NAME = "vector-1741865904949-semantic-configuration"  # ✅ Use for new index
    # SEMANTIC_CONFIG_NAME = "azureml-default"  # ✅ Use for old index

    # 🔹 CHOOSE CONTENT FIELD (Comment/Uncomment as needed)
    CONTENT_FIELD = "chunk"  # ✅ Use for new index
    # CONTENT_FIELD = "content"  # ✅ Use for old index

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        # 🔹 Perform the search with explicit field selection
        logging.info(f"🔍 Searching in Index: {INDEX_NAME}")
        results = search_client.search(
            search_text=user_question,
            query_type="semantic",
            semantic_configuration_name=SEMANTIC_CONFIG_NAME,
            top=top_k,
            select=["title", CONTENT_FIELD],  # ✅ Ensure the correct content field is retrieved
            include_total_count=False
        )

        # Keep original logic of collecting snippets:
        relevant_texts = []
        # Collect docs so we can do weighting:
        docs = []

        for r in results:
            snippet = r.get(CONTENT_FIELD, "").strip()
            title = r.get("title", "").strip()
            if snippet:  # Avoid empty results
                relevant_texts.append(snippet)
                docs.append({"title": title, "snippet": snippet})

        if not relevant_texts:
            return {"top_k": "No information"}

        # 🔹 Apply weighting based on keywords in title (case-insensitive)
        for doc in docs:
            ttl = doc["title"].lower()
            score = 0
            if "policy" in ttl:
                score += 10
            if "report" in ttl:
                score += 5
            if "sop" in ttl:
                score += 3
            doc["weight_score"] = score

        # 🔹 Sort docs by descending weight
        docs_sorted = sorted(docs, key=lambda x: x["weight_score"], reverse=True)

        # 🔹 Slice top_k after re-ranking
        docs_top_k = docs_sorted[:top_k]

        # Prepare final combined text as before:
        re_ranked_texts = [d["snippet"] for d in docs_top_k]
        combined = "\n\n---\n\n".join(re_ranked_texts)

        return {"top_k": combined}

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}


#########################################################################
# Execute Python code inside an isolated environment
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def execute_generated_code(code_str):
    """
    Runs the code string with dataframes loaded from Azure Blob Storage.
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

        # Replace read calls with references to dataframes dict
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
        logging.error(f"Error executing generated code: {str(e)}")
        return f"An error occurred during code execution: {e}"

#########################################################################
# Tool 2: Generate Python code from LLM (no streaming) and run it
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    # Decide if user question references tabular data
    need_data = references_tabular_data(user_question, TABLES, chat_history)
    if not need_data:
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
2. dont use the row samples provided. They are just samples and other rows exist that were not provided to you. all you need to do is check the tables and columns and data types to make the code.
3. Only return pure Python code that is functional and ready to be executed, including the imports if needed.
4. Always make code that returns a print statement that answers the question.

User question:
{user_question}

Dataframes schemas:
{SCHEMA_TEXT}

Dataframes samples:
{SAMPLE_TEXT}

Chat_history:
{chat_history[-4:]}
"""

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 1200,
        "temperature": 0.7
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        # Expect code in data["choices"][0]["message"]["content"]
        code_str = data["choices"][0]["message"]["content"]
        code_str = code_str.strip()

        # If LLM yields '404' or empty code => no info
        if not code_str or "404" in code_str:
            return {"result": "No information", "code": ""}

        execution_result = execute_generated_code(code_str)
        return {"result": execution_result, "code": code_str}

    except Exception as ex:
        logging.error(f"Error in Tool2 (Code Generation/Execution): {str(ex)}")
        return {"result": "No information", "code": ""}

#########################################################################
# Tool 3: Fallback LLM (general knowledge, no data usage)
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_3_llm_fallback(user_question):
    """
    If no data from the index or python was found, fallback to a general knowledge approach.
    No streaming, direct request.
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    system_prompt = (
        "You are a highly knowledgeable large language model. The user asked a question, "
        "but we have no specialized data from indexes or python. Provide a concise, direct answer "
        "using your general knowledge. Do not say 'No information was found'; just answer as best you can."
    )

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 500,
        "temperature": 0.7
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        fallback_answer = data["choices"][0]["message"]["content"].strip()
        return fallback_answer

    except Exception as e:
        logging.error(f"Fallback LLM error: {e}")
        return "I'm sorry, but I couldn't retrieve a fallback answer."

#########################################################################
# Combine index data + python data, produce final answer
#########################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def final_answer_llm(user_question, index_dict, python_dict):
    """
    Merges info from index search + python code results, and forms final answer.
    No streaming, single request approach.
    """
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    # If both have no info => fallback LLM
    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        return f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"

    system_prompt = f"""
You are a helpful assistant. The user asked a question, and you have two data sources:
1) Index data: (INDEX_DATA)
2) Python data: (PYTHON_DATA)

Use only these two sources to answer. If you find relevant info from both, answer using both. 
At the end of your final answer, put EXACTLY one line with "Source: X" where X can be:
- "Index" if only index data was used,
- "Python" if only python data was used,
- "Index & Python" if both were used,
- or "No information was found in the Data. Can I help you with anything else?" if none is relevant.

If multiple sub-questions exist, address them all. Then pick the correct source label.
If there's conflicting info, mention it clearly.

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
        "temperature": 0.0
    }

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        final_text = data["choices"][0]["message"]["content"].strip()
        if not final_text:
            return "No information was found in the Data. Can I help you with anything else?"
        return final_text
    except Exception as e:
        logging.error(f"Error in final_answer_llm: {str(e)}")
        return "An error occurred while processing your request."

#########################################################################
# Append code/text to final answer if source is "Index" or "Python" etc.
#########################################################################
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

#########################################################################
# Determine if user message is just greeting
#########################################################################
def is_entirely_greeting_or_punc(phrase):
    greet_words = {
        "hello", "hi", "hey", "morning", "evening", "goodmorning", "goodevening",
        "assalam", "hayo", "hola", "salam", "alsalam", "alsalamualaikum", "assalamualaikum",
        "greetings", "howdy", "whatsup", "sup", "namaste", "shalom", "bonjour", "ciao",
        "konichiwa", "nihao", "marhaba", "ahlan", "sawubona", "hallo", "salut", "holaamigo",
        "heythere", "goodday", "goodafternoon", "yo"
    }
    tokens = re.findall(r"[A-Za-z]+", phrase.lower())
    if not tokens:
        return False
    for t in tokens:
        if t not in greet_words:
            return False
    return True

#########################################################################
# Main function to produce answer
#########################################################################
def agent_answer(user_question):
    
    # If user_question is empty or just whitespace
    if not user_question.strip():
        return "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        
    # Check for repeated question in cache
    if user_question in tool_cache:
        return tool_cache[user_question]

    # Quick greeting check
    if is_entirely_greeting_or_punc(user_question.strip()):
        # Return short greeting response
        if len(chat_history) < 1:
            result = (
                "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n"
                "- To reset the conversation type 'restart chat'.\n"
                "- To generate Slides, Charts or Document, type 'export followed by your requirements."
            )
        else:
            result = (
                "Hello! How may I assist you?\n"
                "-To reset the conversation type 'restart chat'.\n"
                "-To generate Slides, Charts or Document, type 'export followed by your requirements."
            )
        tool_cache[user_question] = result
        return result

    # Otherwise, normal flow:
    index_dict = tool_1_index_search(user_question)
    python_dict = tool_2_code_run(user_question)
    final_ans = final_answer_llm(user_question, index_dict, python_dict)
    final_ans_with_src = post_process_source(final_ans, index_dict, python_dict)

    # Store in cache
    tool_cache[user_question] = final_ans_with_src
    return final_ans_with_src

#########################################################################
# Logging and Topic function 
#########################################################################
def Classify_Topic(question, answer_text, chat_history):
    """
    Classifies the question into one of these topics:
    (Policy, SOP, Report, Analysis, Exporting_file, Other)
    using the last 4 lines of chat history.
    """
    LLM_ENDPOINT = "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    # Gather up to the last 4 lines of chat
    recent_history = "\n".join(chat_history[-4:])

    system_prompt = (
        "You are a topic classifier. You will receive:\n"
        "1) A user question\n"
        "2) The assistant's answer\n"
        "3) Up to 4 lines of previous chat\n"
        "You must classify the overall topic of the user's question into exactly one of the following:\n"
        "    Policy, SOP, Report, Analysis, Exporting_file, Other\n"
        "Return only the topic word. No explanations."
    )
    user_prompt = (
        f"Question: {question}\nAnswer: {answer_text}\n\nRecent history:\n{recent_history}\n\n"
        "What is the single best-fitting topic label?"
    )

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 50,
        "temperature": 0.0
    }

    headers = {"Content-Type": "application/json", "api-key": LLM_API_KEY}

    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        raw_topic = data["choices"][0]["message"]["content"].strip()
        valid_topics = ["Policy", "SOP", "Report", "Analysis", "Exporting_file", "Other"]
        return raw_topic if raw_topic in valid_topics else "Other"
    except:
        return "Other"


def Log_Interaction(question, final_answer):
    """
    Logs the Q&A interaction to Azure Blob Storage, including:
    - time
    - question
    - answer_text (everything before "Source:")
    - source (the exact text following "Source:", e.g. "Index", "Python", or "Index & Python")
    - source_material (all text after that same "Source:" line)
    - conversation_length
    - topic
    - user_id
    """
    from azure.storage.blob import BlobServiceClient
    from datetime import datetime

    # Azure Blob Storage credentials
    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"

    global chat_history

    # 1) Split final_answer into lines
    lines = final_answer.strip().split("\n")
    # 2) Initialize defaults
    pure_answer = final_answer
    source = "N/A"
    source_material = "N/A"

    # 3) Find the line that starts with "Source:"
    source_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("source:"):
            source_idx = i
            break

    # 4) If found, parse out "Source" and "Source material"
    if source_idx is not None:
        src_line = lines[source_idx].strip()  # e.g., "Source: Index & Python"
        # Remove "Source:" prefix, leaving "Index", "Python", or "Index & Python" etc.
        source_val = src_line[7:].strip()  # everything after "Source:"
        source = source_val if source_val else "N/A"

        # If there's more lines below the "Source:" line, that is the source_material
        if source_idx + 1 < len(lines):
            source_material = "\n".join(lines[source_idx + 1:]).strip()

        # The pure answer is everything above the "Source:" line
        pure_answer = "\n".join(lines[:source_idx]).strip()

    # 5) Count conversation length
    conversation_length = len(chat_history)

    # 6) Determine the topic from the portion we consider the "answer"
    topic = Classify_Topic(question, pure_answer, chat_history)

    # Initialize blob client
    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)

    # Build the log filename (daily CSV)
    target_folder_path = "UI/2024-11-20_142337_UTC/cxqa_data/logs/"
    date_str = datetime.now().strftime("%Y_%m_%d")
    log_filename = f"logs_{date_str}.csv"
    blob_name = target_folder_path + log_filename
    blob_client = container_client.get_blob_client(blob_name)

    # Download existing logs (if any); otherwise create headers
    try:
        existing_data = blob_client.download_blob().readall().decode("utf-8")
        lines_csv = existing_data.strip().split("\n")
        if not lines_csv or not lines_csv[0].startswith("time,question,answer_text,source,source_material,conversation_length,topic,user_id"):
            lines_csv = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]
    except:
        lines_csv = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]

    # Create the new row
    current_time = datetime.now().strftime("%H:%M:%S")
    row = [
        current_time,
        question.replace('"','""'),
        pure_answer.replace('"','""'),
        source.replace('"','""'),
        source_material.replace('"','""'),
        str(conversation_length),
        topic.replace('"','""'),
        "anonymous"
    ]
    lines_csv.append(",".join(f'"{x}"' for x in row))
    new_csv_content = "\n".join(lines_csv) + "\n"

    # Upload the updated log file
    blob_client.upload_blob(new_csv_content, overwrite=True)

#########################################################################
# Public-facing function to handle Q&A and log
#########################################################################
def Ask_Question(question):
    """
    Top-level function to handle user input.
    - If 'export', call export logic.
    - If 'restart chat', clear conversation.
    - Otherwise, perform normal Q&A and log with Log_Interaction.
    """
    global chat_history
    q_lower = question.lower().strip()

    # Handle export requests
    if q_lower.startswith("export"):
        from Export_Agent import Call_Export
        instructions = question[6:].strip()
        if len(chat_history) >= 2:
            latest_answer = chat_history[-1]
            latest_question = chat_history[-2]
        else:
            latest_answer = "No previous answer available."
            latest_question = "No previous question available."
        export_result = Call_Export(latest_question, latest_answer, chat_history, instructions)
        yield export_result
        return

    # Handle "restart chat"
    if q_lower in ["restart chat", "reset chat", "restart the chat", "reset the chat", "start over"]:
        chat_history.clear()
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # Normal Q&A
    chat_history.append(f"User: {question}")
    final_answer = agent_answer(question)
    chat_history.append(f"Assistant: {final_answer}")

    # Trim chat_history if needed
    if len(chat_history) > 12:
        chat_history = chat_history[-12:]

    # Logging
    Log_Interaction(question, final_answer)

    # Return final answer
    yield final_answer
