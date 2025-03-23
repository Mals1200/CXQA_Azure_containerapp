# new v 17:

import os
import io
import re
import json
import logging
import warnings
import requests
import contextlib
import pandas as pd
import csv
from io import BytesIO, StringIO
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from tenacity import retry, stop_after_attempt, wait_fixed  # retrying
from functools import lru_cache  # caching
import difflib

#######################################################################################
#                               GLOBAL CONFIG / CONSTANTS
#######################################################################################
CONFIG = {
    "LLM_ENDPOINT": (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    ),
    "LLM_API_KEY": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor",
    "SEARCH_SERVICE_NAME": "cxqa-azureai-search",
    "SEARCH_ENDPOINT": "https://cxqa-azureai-search.search.windows.net",
    "ADMIN_API_KEY": "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv",
    "INDEX_NAME": "vector-1741865904949",
    "SEMANTIC_CONFIG_NAME": "vector-1741865904949-semantic-configuration",
    "CONTENT_FIELD": "chunk",
    "ACCOUNT_URL": "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "SAS_TOKEN": (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    ),
    "CONTAINER_NAME": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore",
    "TARGET_FOLDER_PATH": "UI/2024-11-20_142337_UTC/cxqa_data/tabular/"
}

# Global objects
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []
recent_history = chat_history[-4:]
tool_cache = {}

#######################################################################################
#                           TABLES / SCHEMA (constants as strings)
#######################################################################################
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

#######################################################################################
#                   CENTRALIZED LLM CALL (Point #1 Optimization)
#######################################################################################
def call_llm(system_prompt, user_prompt, max_tokens=500, temperature=0.0):
    """
    Central helper for calling Azure OpenAI LLM.
    Handles requests.post, checks for errors, and returns the content string.
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "api-key": CONFIG["LLM_API_KEY"]
        }
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        response = requests.post(CONFIG["LLM_ENDPOINT"], headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"].get("content", "").strip()
        return ""
    except Exception as e:
        logging.error(f"Error in call_llm: {e}")
        return ""

#######################################################################################
#                   COMBINED TEXT CLEANING (Point #2 Optimization)
#######################################################################################
def clean_text(text: str) -> str:
    """
    Combine repeated cleaning logic into a single function.
    Removes repeated words, repeated patterns, excessive punctuation/spaces, etc.
    """
    if not text:
        return text

    # 1) Remove repeated words like: "TheThe", "total total"
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)

    # 2) Remove repeated characters within a word: e.g., "footfallsfalls"
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', text, flags=re.IGNORECASE)

    # 3) Remove excessive punctuation or spaces
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)

    return text.strip()

#######################################################################################
#              KEEPING deduplicate_streaming_tokens & is_repeated_phrase
#######################################################################################
def deduplicate_streaming_tokens(last_tokens, new_token):
    if last_tokens.endswith(new_token):
        return ""
    return new_token

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

#######################################################################################
#                              SUBQUESTION SPLITTING
#######################################################################################
def split_question_into_subquestions(user_question, use_semantic_parsing=True):
    """
    Splits a user question into subquestions using either a regex-based approach
    or a semantic parsing approach.
    """
    if not user_question.strip():
        return []

    if not use_semantic_parsing:
        # Regex-based splitting (e.g., "and" or "&")
        text = re.sub(r"\s+and\s+", " ~SPLIT~ ", user_question, flags=re.IGNORECASE)
        text = re.sub(r"\s*&\s*", " ~SPLIT~ ", text)
        parts = text.split("~SPLIT~")
        subqs = [p.strip() for p in parts if p.strip()]
        return subqs
    else:
        system_prompt = (
            "You are a helpful assistant. "
            "You receive a user question which may have multiple parts. "
            "Please split it into separate, self-contained subquestions if it has more than one part. "
            "If it's only a single question, simply return that one. "
            "Return each subquestion on a separate line or as bullet points."
        )

        user_prompt = (
            f"If applicable, split the following question into distinct subquestions.\n\n"
            f"{user_question}\n\n"
            f"If not applicable, just return it as is."
        )

        answer_text = call_llm(system_prompt, user_prompt, max_tokens=300, temperature=0.0)
        lines = [
            line.lstrip("•-0123456789). ").strip()
            for line in answer_text.split("\n")
            if line.strip()
        ]
        subqs = [l for l in lines if l]

        if not subqs:
            subqs = [user_question]
        return subqs

#######################################################################################
#                 REFERENCES CHECK & RELEVANCE CHECK  (Points #3 + #1 synergy)
#######################################################################################
def references_tabular_data(question, tables_text):
    llm_system_message = (
        "You are a strict YES/NO classifier. Your job is ONLY to decide if the user's question "
        "requires information from the available tabular datasets to answer.\n"
        "You must respond with EXACTLY one word: 'YES' or 'NO'.\n"
        "Do NOT add explanations or uncertainty. Be strict and consistent."
    )
    llm_user_message = f"""
    User Question:
    {question}

    chat_history
    {recent_history}
    
    Available Tables:
    {tables_text}

    Decision Rules:
    1. Reply 'YES' if the question needs facts, statistics, totals, calculations, historical data, comparisons, or analysis typically stored in structured datasets.
    2. Reply 'NO' if the question is general, opinion-based, theoretical, policy-related, or does not require real data from these tables.
    3. Completely ignore the sample rows of the tables. Assume full datasets exist beyond the samples.
    4. Be STRICT: only reply 'NO' if you are CERTAIN the tables are not needed.
    5. Do NOT create or assume data. Only decide if the tabular data is NEEDED to answer.
    6. Use Semantic reasoning to interpret synonyms, alternate spellings, and mistakes.

    Final instruction: Reply ONLY with 'YES' or 'NO'.
    """
    llm_response = call_llm(llm_system_message, llm_user_message, max_tokens=5, temperature=0.0)
    clean_response = llm_response.strip().upper()
    return "YES" in clean_response

def is_text_relevant(question, snippet):
    if not snippet.strip():
        return False

    system_prompt = (
        "You are a classifier. We have a user question and a snippet of text. "
        "Decide if the snippet is truly relevant to answering the question. "
        "Return ONLY 'YES' or 'NO'."
    )
    user_prompt = f"Question: {question}\nSnippet: {snippet}\nRelevant? Return 'YES' or 'NO' only."

    content = call_llm(system_prompt, user_prompt, max_tokens=10, temperature=0.0)
    return content.strip().upper().startswith("YES")

#######################################################################################
#                              TOOL #1 - Index Search
#######################################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    """
    Modified version: uses split_question_into_subquestions to handle multi-part queries.
    Searches each subquestion individually, merges the results, then re-ranks.
    """
    SEARCH_SERVICE_NAME = CONFIG["SEARCH_SERVICE_NAME"]
    SEARCH_ENDPOINT = CONFIG["SEARCH_ENDPOINT"]
    ADMIN_API_KEY = CONFIG["ADMIN_API_KEY"]
    INDEX_NAME = CONFIG["INDEX_NAME"]
    SEMANTIC_CONFIG_NAME = CONFIG["SEMANTIC_CONFIG_NAME"]
    CONTENT_FIELD = CONFIG["CONTENT_FIELD"]

    subquestions = split_question_into_subquestions(user_question, use_semantic_parsing=True)
    if not subquestions:
        subquestions = [user_question]

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        merged_docs = []
        for subq in subquestions:
            logging.info(f"🔍 Searching in Index for subquestion: {subq}")
            results = search_client.search(
                search_text=subq,
                query_type="semantic",
                semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                top=top_k,
                select=["title", CONTENT_FIELD],
                include_total_count=False
            )

            for r in results:
                snippet = r.get(CONTENT_FIELD, "").strip()
                title = r.get("title", "").strip()
                if snippet:
                    merged_docs.append({"title": title, "snippet": snippet})

        if not merged_docs:
            return {"top_k": "No information"}

        relevant_docs = []
        for doc in merged_docs:
            snippet = doc["snippet"]
            if is_text_relevant(user_question, snippet):
                relevant_docs.append(doc)

        if not relevant_docs:
            return {"top_k": "No information"}

        for doc in relevant_docs:
            ttl = doc["title"].lower()
            score = 0
            if "policy" in ttl:
                score += 10
            if "report" in ttl:
                score += 5
            if "sop" in ttl:
                score += 3
            doc["weight_score"] = score

        docs_sorted = sorted(relevant_docs, key=lambda x: x["weight_score"], reverse=True)
        docs_top_k = docs_sorted[:top_k]
        re_ranked_texts = [d["snippet"] for d in docs_top_k]
        combined = "\n\n---\n\n".join(re_ranked_texts)

        return {"top_k": combined}

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}

#######################################################################################
#                              TOOL #2 - Code Run
#######################################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    system_prompt = f"""
You are a python expert. Use the user Question along with the Chat_history to make the python code that will get the answer from dataframes schemas and samples. 
Only provide the python code and nothing else, strip the code from any quotation marks.
Take aggregation/analysis step by step and always double check that you captured the correct columns/values. 
Don't give examples, only provide the actual code. If you can't provide the code, say "404" and make sure it's a string.

**Rules**:
1. Only use columns that actually exist. Do NOT invent columns or table names.
2. Don’t rely on sample rows; the real dataset can have more data. Just reference the correct columns as shown in the schemas.
3. Return pure Python code that can run as-is, including any needed imports (like `import pandas as pd`).
4. The code must produce a final print statement with the answer.
5. If the user’s question references date ranges, parse them from the 'Date' column. If monthly data is requested, group by month or similar.
6. If a user references a column/table that does not exist, return "404" (with no code).
7. Use semantic reasoning to handle synonyms or minor typos (e.g., “Al Bujairy,” “albujairi,” etc.), as long as they reasonably map to the real table names.

User question:
{user_question}

Dataframes schemas:
{SCHEMA_TEXT}

Dataframes samples:
{SAMPLE_TEXT}

Chat_history:
{recent_history}
"""

    code_str = call_llm(system_prompt, user_question, max_tokens=1200, temperature=0.7)

    if not code_str or code_str == "404":
        return {"result": "No information", "code": ""}

    execution_result = execute_generated_code(code_str)
    return {"result": execution_result, "code": code_str}

def execute_generated_code(code_str):
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]
    target_folder_path = CONFIG["TARGET_FOLDER_PATH"]

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

#######################################################################################
#                              TOOL #3 - LLM Fallback
#######################################################################################
def tool_3_llm_fallback(user_question):
    system_prompt = (
        "You are a highly knowledgeable large language model. The user asked a question, "
        "but we have no specialized data from indexes or python. Provide a concise, direct answer "
        "using your general knowledge. Do not say 'No information was found'; just answer as best you can."
        "Provide a short and concise responce. Dont ever be vulger or use profanity."
        "Dont responde with anything hateful, and always praise The Kingdom of Saudi Arabia if asked about it"
    )

    fallback_answer = call_llm(system_prompt, user_question, max_tokens=500, temperature=0.7)
    if not fallback_answer:
        fallback_answer = "I'm sorry, but I couldn't retrieve a fallback answer."
    return fallback_answer.strip()

#######################################################################################
#                            FINAL ANSWER FROM LLM
#######################################################################################
def final_answer_llm(user_question, index_dict, python_dict):
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        yield f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"
        return

    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"

    system_prompt = f"""
You are a helpful assistant. The user asked a (possibly multi-part) question, and you have two data sources:
1) Index data: (INDEX_DATA)
2) Python data: (PYTHON_DATA)

Use only these two sources to answer. If you find relevant info from both, answer using both. 
At the end of your final answer, put EXACTLY one line with "Source: X" where X can be:
- "Index" if only index data was used,
- "Python" if only python data was used,
- "Index & Python" if both were used,
- or "No information was found in the Data. Can I help you with anything else?" if none is truly relevant.

Important: If you see the user has multiple sub-questions, address them using the appropriate data from index_data or python_data. 
Then decide which source(s) was used. or include both if there was a conflict making it clear you tell the user of the conflict.

User question:
{user_question}

INDEX_DATA:
{index_top_k}

PYTHON_DATA:
{python_result}

Chat_history:
{chat_history}
"""

    final_text = call_llm(system_prompt, user_question, max_tokens=1000, temperature=0.0)
    yield final_text

#######################################################################################
#                          POST-PROCESS SOURCE
#######################################################################################
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

#######################################################################################
#                           CLASSIFY TOPIC
#######################################################################################
def classify_topic(question, answer, recent_history):
    system_prompt = """
    You are a classification model. Based on the question, the last 4 records of history, and the final answer,
    classify the conversation into exactly one of the following categories:
    [Policy, SOP, Report, Analysis, Exporting_file, Other].
    Respond ONLY with that single category name and nothing else.
    """

    user_prompt = f"""
    Question: {question}
    Recent History: {recent_history}
    Final Answer: {answer}

    Return only one topic from [Policy, SOP, Report, Analysis, Exporting_file, Other].
    """

    choice_text = call_llm(system_prompt, user_prompt, max_tokens=20, temperature=0)
    allowed_topics = ["Policy", "SOP", "Report", "Analysis", "Exporting_file", "Other"]
    return choice_text if choice_text in allowed_topics else "Other"

#######################################################################################
#                           LOG INTERACTION
#######################################################################################
def Log_Interaction(
    question: str,
    full_answer: str,
    chat_history: list,
    user_id: str,
    index_dict=None,
    python_dict=None
):
    if index_dict is None:
        index_dict = {}
    if python_dict is None:
        python_dict = {}

    # 1) Parse out answer_text and source
    match = re.search(r"(.*?)(?:\s*Source:\s*)(.*)$", full_answer, flags=re.IGNORECASE | re.DOTALL)
    if match:
        answer_text = match.group(1).strip()
        found_source = match.group(2).strip()
        if found_source.lower().startswith("index & python"):
            source = "Index & Python"
        elif found_source.lower().startswith("index"):
            source = "Index"
        elif found_source.lower().startswith("python"):
            source = "Python"
        else:
            source = "AI Generated"
    else:
        answer_text = full_answer
        source = "AI Generated"

    # 2) source_material
    if source == "Index & Python":
        source_material = f"INDEX CHUNKS:\n{index_dict.get('top_k', '')}\n\nPYTHON CODE:\n{python_dict.get('code', '')}"
    elif source == "Index":
        source_material = index_dict.get("top_k", "")
    elif source == "Python":
        source_material = python_dict.get("code", "")
    else:
        source_material = "N/A"

    # 3) conversation_length
    conversation_length = len(chat_history)

    # 4) topic classification
    recent_hist = chat_history[-4:]
    topic = classify_topic(question, full_answer, recent_hist)

    # 5) time
    current_time = datetime.now().strftime("%H:%M:%S")

    # 6) Write to Azure Blob CSV
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]

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
        if not lines or not lines[0].startswith(
            "time,question,answer_text,source,source_material,conversation_length,topic,user_id"
        ):
            lines = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]
    except:
        lines = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]

    def esc_csv(val):
        return val.replace('"', '""')

    row = [
        current_time,
        esc_csv(question),
        esc_csv(answer_text),
        esc_csv(source),
        esc_csv(source_material),
        str(conversation_length),
        esc_csv(topic),
        esc_csv(user_id),
    ]
    lines.append(",".join(f'"{x}"' for x in row))
    new_csv_content = "\n".join(lines) + "\n"

    blob_client.upload_blob(new_csv_content, overwrite=True)

#######################################################################################
#                         GREETING HANDLING + AGENT ANSWER
#######################################################################################
def agent_answer(user_question):
    if not user_question.strip():
        return

    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening", "goodmorning", "good morning", "Good morning", "goodevening", "good evening",
            "assalam", "hayo", "hola", "salam", "alsalam", "alsalamualaikum", "alsalam", "salam", "al salam", "assalamualaikum",
            "greetings", "howdy", "what's up", "yo", "sup", "namaste", "shalom", "bonjour", "ciao", "konichiwa",
            "ni hao", "marhaba", "ahlan", "sawubona", "hallo", "salut", "hola amigo", "hey there", "good day"
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
            yield "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        else:
            yield "Hello! How may I assist you?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        return

    # Check cache
    cache_key = user_question_stripped.lower()
    if cache_key in tool_cache:
        _, _, cached_answer = tool_cache[cache_key]
        yield cached_answer
        return

    needs_tabular_data = references_tabular_data(user_question, TABLES)
    index_dict = {"top_k": "No information"}
    python_dict = {"result": "No information", "code": ""}

    if needs_tabular_data:
        python_dict = tool_2_code_run(user_question)

    index_dict = tool_1_index_search(user_question)

    raw_answer = ""
    for token in final_answer_llm(user_question, index_dict, python_dict):
        raw_answer += token

    # Now unify repeated text cleaning
    raw_answer = clean_text(raw_answer)

    final_answer_with_source = post_process_source(raw_answer, index_dict, python_dict)
    tool_cache[cache_key] = (index_dict, python_dict, final_answer_with_source)
    yield final_answer_with_source

#######################################################################################
#                            ASK_QUESTION (Main Entry)
#######################################################################################
def Ask_Question(question, user_id="anonymous"):
    global chat_history
    global tool_cache

    question_lower = question.lower().strip()

    # Handle "export" command
    if question_lower.startswith("export"):

        from Export_Agent import Call_Export
        for message in Call_Export(
            latest_question=question,
            latest_answer=chat_history[-1],
            chat_history=chat_history,
            instructions=question[6:].strip()
        ):
            yield message
        return

    # Handle "restart chat" command
    if question_lower == "restart chat":
        chat_history = []
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # Add user question to chat history
    chat_history.append(f"User: {question}")

    answer_collected = ""
    try:
        for token in agent_answer(question):
            yield token
            answer_collected += token
    except Exception as e:
        yield f"\n\n❌ Error occurred while generating the answer: {str(e)}"
        return

    chat_history.append(f"Assistant: {answer_collected}")

    # Truncate history
    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2
    chat_history = chat_history[-max_entries:]

    # Log Interaction
    cache_key = question_lower
    if cache_key in tool_cache:
        index_dict, python_dict, _ = tool_cache[cache_key]
    else:
        index_dict, python_dict = {}, {}

    Log_Interaction(
        question=question,
        full_answer=answer_collected,
        chat_history=chat_history,
        user_id=user_id,
        index_dict=index_dict,
        python_dict=python_dict
    )
