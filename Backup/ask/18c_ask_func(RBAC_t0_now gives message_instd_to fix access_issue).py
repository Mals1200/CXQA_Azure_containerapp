# Version 18c:
# =========================================================================================
#                                CHANGELOG (Text Tier RBAC UPDATE)
# =========================================================================================
# 1. Added text_tier.xlsx Lookup:
#    - get_text_tier_data(): Reads from text_tier.xlsx (Doc_Name, Tier_Level) to map doc titles -> required tier.
#    - Instead of relying on doc["tier"], we use text_tier_map.get(title, "t1") in tool_1_index_search.
#
# 2. Retained table-based RBAC from rbac.xlsx for tool_2_code_run.
#
# 3. tool_1_index_search:
#    - Removed reliance on doc['tier'] from the index. Now we do:
#         doc_tier = text_tier_map.get(title, "t1")
#      Then check if doc_tier is in user’s allowed tiers.
#
# 4. If user tier == 't0', we now return "You do not have access to this Service." immediately.

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
    Improved to ensure we do not return an empty string silently.
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
            content = data["choices"][0]["message"].get("content", "").strip()
            if content:
                return content
            else:
                logging.warning("LLM returned an empty content field.")
                return "No content from LLM."
        else:
            logging.warning(f"LLM returned no choices: {data}")
            return "No choices from LLM."
    except Exception as e:
        logging.error(f"Error in call_llm: {e}")
        return f"LLM Error: {e}"

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
#             NEW: LOAD text_tier.xlsx FOR DOC TIER (REPLACES doc["tier"] FIELD)
#######################################################################################
@lru_cache(maxsize=None)
def get_text_tier_data():
    """
    Loads "text_tier.xlsx" from the same RBAC folder, containing columns:
       Doc_Name, Tier_Level
    E.g.:
        Doc_Name           Tier_Level
        Fire_Safety        t3
        Evacuation_Plan    t4
    Returns a dictionary: { "Fire_Safety" : "t3", ... }
    We'll match 'title' from the index to Doc_Name. If not found, default t1.
    """
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]
    blob_path = "UI/2024-11-20_142337_UTC/cxqa_data/RBAC/text_tier.xlsx"  # Replace if needed

    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)

    data = blob_client.download_blob().readall()
    df = pd.read_excel(BytesIO(data), sheet_name=0)

    text_tier_map = {}
    # Expect columns: "Doc_Name", "Tier_Level"
    for _, row in df.iterrows():
        doc_name = str(row["Doc_Name"]).strip()
        doc_tier = str(row["Tier_Level"]).strip().lower()  # e.g., "t3"
        text_tier_map[doc_name] = doc_tier

    return text_tier_map

#######################################################################################
#        NEW: RBAC LOOKUP & HELPER FUNCTIONS TO DETERMINE USER + TABLE TIERS
#######################################################################################
@lru_cache(maxsize=None)
def get_rbac_data():
    """
    Reads the 'rbac.xlsx' from the same container/folder.  
    We assume it has:
      - A sheet or area with columns: [User_ID, Tier_Level]
      - A sheet or area with columns: [Table_Name, Table_Tier]
    Returns:
      user_tier_map = {user_id -> 't1'/'t2'/...} 
      table_tier_map = {table_name -> 't1'/'t2'/...}
    """
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]
    blob_path = "UI/2024-11-20_142337_UTC/cxqa_data/RBAC/rbac.xlsx"  # provided path

    blob_service
