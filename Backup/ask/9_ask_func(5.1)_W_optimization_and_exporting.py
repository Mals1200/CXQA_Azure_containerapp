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
from tenacity import retry, stop_after_attempt, wait_fixed #retrying
from functools import lru_cache #caching
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

def clean_repeated_phrases(text):
    """
    Removes repeated words like 'TheThe' or 'total total'.
    """
    return re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)

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
Al-Bujairy Terrace Footfalls.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Footfalls': 2950}, {'Date': "Timestamp('2023-01-02 00:00:00')", 'Footfalls': 2864}, {'Date': "Timestamp('2023-01-03 00:00:00')", 'Footfalls': 4366}],
Al-Turaif Footfalls.xlsx: [{'Date': "Timestamp('2023-06-01 00:00:00')", 'Footfalls': 694}, {'Date': "Timestamp('2023-06-02 00:00:00')", 'Footfalls': 1862}, {'Date': "Timestamp('2023-06-03 00:00:00')", 'Footfalls': 1801}],
Complaints.xlsx: [{'Created On': "Timestamp('2024-01-01 00:00:00')", 'Incident Category': 'Contact Center Operation', 'Status': 'Resolved', 'Resolved On Date(Local)': datetime.datetime(2024, 1, 1, 0, 0), 'Incident Description': 'Message: السلام عليكم ورحمة الله وبركاته، مساء الخير م', 'Resolution': 'ضيفنا العزيز،نشكر لكم تواصلكم معنافيما يخص طلبكم في فرص التدريب التعاوني يرجى رفع طلبكم عبر موقع هيئة تطوير بوابة الدرعية Career | Diriyah Gate Development Authority (dgda.gov.sa)نتشرف بخدمتكم'}, {'Created On': "Timestamp('2024-01-01 00:00:00')", 'Incident Category': 'Roads and Infrastructure', 'Status': 'Resolved', 'Resolved On Date(Local)': datetime.datetime(2024, 1, 8, 0, 0), 'Incident Description': 'test', 'Resolution': 'test'}, {'Created On': "Timestamp('2024-01-01 00:00:00')", 'Incident Category': 'Security and Safety', 'Status': 'Resolved', 'Resolved On Date(Local)': datetime.datetime(2024, 1, 1, 0, 0), 'Incident Description': 'Test', 'Resolution': 'Test'}],
Duty manager log.xlsx: [{'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", 'Shift': 'Morning Shift', 'Issue': 'Hakkassan and WC5', 'Department': 'Operation', 'Team': 'Operation', 'Incident': ' Electricity box in WC5 have water and its under maintenance, its effected hakkasan and WC5 (WC5 closed)', 'Remark': 'FM has been informed ', 'Status': 'Pending', 'ETA': 'Please FM update the ETA', 'Days': nan}, {'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", 'Shift': 'Morning Shift', 'Issue': 'flamingo', 'Department': 'Operation', 'Team': 'Operation', 'Incident': '\\nWe received a massage from flamingo manager regarding to some points needs to be fixed in the restaurant, painting,doors,ropes,canopys,scratch and cracks,varnishing, some of the points been shared to FM before.', 'Remark': 'The pictures been sent to FM', 'Status': 'Pending', 'ETA': 'Please FM update the ETA', 'Days': 7.0}, {'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", 'Shift': 'Morning Shift', 'Issue': 'Al Habib Hospital', 'Department': 'Operation', 'Team': 'Operation', 'Incident': '7 Minor incidents  ', 'Remark': nan, 'Status': 'Done', 'ETA': nan, 'Days': nan}],
Food and Beverages (F&b) Sales.xlsx: [{'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': "Timestamp('2023-08-01 00:00:00')", 'Covers': 195.0, 'Gross Sales': 12536.65383}, {'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': "Timestamp('2023-08-02 00:00:00')", 'Covers': 169.0, 'Gross Sales': 11309.05671}, {'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': "Timestamp('2023-08-03 00:00:00')", 'Covers': 243.0, 'Gross Sales': 17058.61479}],
Meta-Data.xlsx: [{'Visitation': 'Revenue', 'Attendance': 'Income', 'Visitors': 'Sales', 'Guests': 'Gross Sales', 'Footfalls': nan, 'Unnamed: 5': nan}, {'Visitation': 'Utilization', 'Attendance': 'Occupancy', 'Visitors': 'Usage Rate', 'Guests': 'Capacity', 'Footfalls': 'Efficiency', 'Unnamed: 5': nan}, {'Visitation': 'Penetration', 'Attendance': 'Covers rate', 'Visitors': 'Restaurants rate', 'Guests': nan, 'Footfalls': nan, 'Unnamed: 5': nan}],
PE Observations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, {'Unnamed: 0': 'Row Labels', 'Unnamed: 1': 'Count of Colleague name'}, {'Unnamed: 0': 'Guest Greetings ', 'Unnamed: 1': 2154}],
Parking.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Valet Volume': 194, 'Valet Revenue': 29100, 'Valet Utilization': 0.23, 'BCP Revenue': '               -  ', 'BCP Volume': 1951, 'BCP Utilization': 0.29, 'SCP Volume': 0, 'SCP Revenue': 0, 'SCP Utilization': 0.0}, {'Date': "Timestamp('2023-01-02 00:00:00')", 'Valet Volume': 223, 'Valet Revenue': 33450, 'Valet Utilization': 0.27, 'BCP Revenue': '               -  ', 'BCP Volume': 1954, 'BCP Utilization': 0.29, 'SCP Volume': 0, 'SCP Revenue': 0, 'SCP Utilization': 0.0}, {'Date': "Timestamp('2023-01-03 00:00:00')", 'Valet Volume': 243, 'Valet Revenue': 36450, 'Valet Utilization': 0.29, 'BCP Revenue': '               -  ', 'BCP Volume': 2330, 'BCP Utilization': 0.35, 'SCP Volume': 0, 'SCP Revenue': 0, 'SCP Utilization': 0.0}],
Qualitative Comments.xlsx: [{'Open Ended': 'يفوقو توقعاتي كل شيء رائع'}, {'Open Ended': 'وقليل اسعار التذاكر اجعل الجميع يستمتع بهذه التجربة الرائعة'}, {'Open Ended': 'إضافة كراسي هامة اكثر من المتوفر'}],
Tenants Violations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, {'Unnamed: 0': 'Row Labels', 'Unnamed: 1': 'Count of Department\\u200b'}, {'Unnamed: 0': 'Lab Test', 'Unnamed: 1': 38}],
Tickets.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Number of tickets': 4644, 'revenue': 288050, 'attendnace': 2950, 'Reservation Attendnace': 0, 'Pass Attendance': 0, 'Male attendance': 1290, 'Female attendance': 1660, 'Rebate value': 131017.96, 'AM Tickets': 287, 'PM Tickets': 2663, 'Free tickets': 287, 'Paid tickets': 2663, 'Free tickets %': 0.09728813559322035, 'Paid tickets %': 0.9027118644067796, 'AM Tickets %': 0.09728813559322035, 'PM Tickets %': 0.9027118644067796, 'Rebate Rate V 55': 131017.96, 'Revenue  v2': 288050}, {'Date': "Timestamp('2023-01-02 00:00:00')", 'Number of tickets': 7276, 'revenue': 205250, 'attendnace': 2864, 'Reservation Attendnace': 0, 'Pass Attendance': 0, 'Male attendance': 1195, 'Female attendance': 1669, 'Rebate value': 123698.68, 'AM Tickets': 978, 'PM Tickets': 1886, 'Free tickets': 978, 'Paid tickets': 1886, 'Free tickets %': 0.3414804469273743, 'Paid tickets %': 0.6585195530726257, 'AM Tickets %': 0.3414804469273743, 'PM Tickets %': 0.6585195530726257, 'Rebate Rate V 55': 123698.68, 'Revenue  v2': 205250}, {'Date': "Timestamp('2023-01-03 00:00:00')", 'Number of tickets': 8354, 'revenue': 308050, 'attendnace': 4366, 'Reservation Attendnace': 0, 'Pass Attendance': 0, 'Male attendance': 1746, 'Female attendance': 2620, 'Rebate value': 206116.58, 'AM Tickets': 1385, 'PM Tickets': 2981, 'Free tickets': 1385, 'Paid tickets': 2981, 'Free tickets %': 0.3172240036646816, 'Paid tickets %': 0.6827759963353184, 'AM Tickets %': 0.3172240036646816, 'PM Tickets %': 0.6827759963353184, 'Rebate Rate V 55': 206116.58, 'Revenue  v2': 308050}],
Top2Box Summary.xlsx: [{'Month': "Timestamp('2024-01-01 00:00:00')", 'Type': 'Bujairi Terrace/ Diriyah  offering', 'Top2Box scores/ rating': 0.669449081803}, {'Month': "Timestamp('2024-01-01 00:00:00')", 'Type': 'Eating out experience', 'Top2Box scores/ rating': 0.7662337662338}, {'Month': "Timestamp('2024-01-01 00:00:00')", 'Type': 'Entrance to Bujairi Terrace', 'Top2Box scores/ rating': 0.7412353923205}],
Total Landscape areas and quantities.xlsx: [{'Assets': 'SN', 'Unnamed: 1': 'Location', 'Unnamed: 2': 'Unit', 'Unnamed: 3': 'Quantity'}, {'Assets': 'Bujairi, Turaif Gardens, and Terraces', 'Unnamed: 1': nan, 'Unnamed: 2': nan, 'Unnamed: 3': nan}, {'Assets': '\\xa0A', 'Unnamed: 1': 'Turaif Gardens', 'Unnamed: 2': nan, 'Unnamed: 3': nan}],
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
    except Exception as e:
        logging.error(f"Error during relevance check: {e}")
        return False

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

    Available Tables:
    {tables_text}

    Decision Rules:
    1. Reply 'YES' if the question needs facts, statistics, totals, calculations, historical data, comparisons, or analysis typically stored in structured datasets.
    2. Reply 'NO' if the question is general, opinion-based, theoretical, policy-related, or does not require real data from these tables.
    3. Completely ignore the sample rows of the tables. Assume full datasets exist beyond the samples.
    4. Be STRICT: only reply 'NO' if you are CERTAIN the tables are not needed.
    5. Do NOT create or assume data. Only decide if the tabular data is NEEDED to answer.

    Final instruction: Reply ONLY with 'YES' or 'NO'.
    """

    payload = {
        "messages": [
            {"role": "system", "content": llm_system_message},
            {"role": "user", "content": llm_user_message}
        ],
        "max_tokens": 5,
        "temperature": 0.0,
        "stream": True
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
        clean_response = llm_response.strip().upper()
        return "YES" in clean_response
    except Exception as e:
        logging.error(f"Error in references_tabular_data: {e}")
        return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
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
        logging.error(f"Error in Tool1 (Index Search): {e}")  # 🔄 Added logging
        return {"top_k": f"Error in Tool1 (Index Search): {str(e)}"}

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
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
        if not code_str or code_str == "404":  # ✅ FIX: Exact match for "404"
            return {"result": "No information", "code": ""}

        execution_result = execute_generated_code(code_str)
        return {"result": execution_result, "code": code_str}

    except Exception as ex:
        logging.error(f"Error in tool_2_code_run: {ex}")  # 🔄 CHANGE: Added error logging
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

def tool_3_llm_fallback(user_question):
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
        yield f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"
        return

    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

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
    recent_output = ""

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
                                if content_piece:
                                    recent_output += content_piece
                                    cleaned_piece = clean_repeated_patterns(recent_output)
                                    new_text = cleaned_piece[len(final_text):]
                                    if new_text:
                                        yield new_text
                                        final_text = cleaned_piece
                        except json.JSONDecodeError:
                            pass
    except:
        yield "\n\nAn error occurred while processing your request."


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

####################################################
#              GREETING HANDLING UPDATED           #
####################################################
def agent_answer(user_question):
    # If question is empty at first usage
    if user_question.strip() == "" and len(chat_history) < 2:
        yield ""

    # A function to see if entire user input is basically a greeting
    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening", "goodmorning", "good morning", "Good morning", "goodevening", "good evening",
            "assalam", "hayo", "hola", "salam", "alsalam",
            "alsalamualaikum", "alsalam", "salam", "al salam", "assalamualaikum",
            "greetings", "howdy", "what's up", "yo", "sup", "namaste", "shalom", "bonjour", "ciao", "konichiwa",
            "ni hao", "marhaba", "ahlan", "sawubona", "hallo", "salut", "hola amigo", "hey there", "good day"
        }
        # Extract alphabetical tokens
        tokens = re.findall(r"[A-Za-z]+", phrase.lower())
        if not tokens:
            return False
        for t in tokens:
            if t not in greet_words:
                return False
        return True

    user_question_stripped = user_question.strip()

    # If entire phrase is basically a greeting
    if is_entirely_greeting_or_punc(user_question_stripped):
        if len(chat_history) < 4:
            yield "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        else:
            yield "Hello! How may I assist you?\n-To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        return

    # ✅ Check cache before doing any work
    cache_key = user_question_stripped.lower()
    if cache_key in tool_cache:
        _, _, cached_answer = tool_cache[cache_key]
        yield cached_answer
        return    
    ####################################################
    # ✅ ENHANCED TOOL SELECTION LOGIC STARTS HERE
    ####################################################

    needs_tabular_data = references_tabular_data(user_question, TABLES)    
    # Otherwise, proceed with normal logic:
    index_dict = {"top_k": "No information"}
    python_dict = {"result": "No information", "code": ""}

    # Conditionally run Python tool if needed
    if needs_tabular_data:
        python_dict = tool_2_code_run(user_question)

    # Always run index search
    index_dict = tool_1_index_search(user_question)

    ####################################################
    # ✅ TOOL SELECTION LOGIC ENDS HERE
    ####################################################

    full_answer = ""

    # ✅ Stream the answer while collecting it
    for token in final_answer_llm(user_question, index_dict, python_dict):
        print(token, end='', flush=True)  # Optional: stream to console
        yield token
        full_answer += token

    # ✅ Clean repeated phrases
    full_answer = clean_repeated_phrases(full_answer)

    # ✅ After streaming completes, apply post-processing to add source, files, and code
    final_answer_with_source = post_process_source(full_answer, index_dict, python_dict)

    # ✅ Cache the result for reuse
    tool_cache[cache_key] = (index_dict, python_dict, final_answer_with_source)

    # ✅ Yield any extra content (source, files, code) added by post-processing
    extra_part = final_answer_with_source[len(full_answer):]
    if extra_part.strip():
        yield "\n\n" + extra_part

def Ask_Question(question):
    global chat_history
    question_lower = question.lower().strip()

    # 1️⃣ Handle export requests
    if question_lower.startswith("export"):
        from Export_Agent import Call_Export

        instructions = question[6:].strip()

        if len(chat_history) >= 2:
            latest_question = chat_history[-1]
            latest_answer = chat_history[-2]
        else:
            yield "Error: Not enough conversation history to perform export. Please ask at least one question first."
            return
        for message in Call_Export(
            latest_question=latest_question,
            latest_answer=latest_answer,
            chat_history=chat_history,
            instructions=instructions
        ):
            yield message 
        return        # Stop here after export

    # 2️⃣ Handle chat restart
    if question_lower == "restart chat":
        chat_history = []
        tool_cache.clear()  # ✅ Clear cache when restarting
        yield "The chat has been restarted."
        return

    # 3️⃣ Handle greetings
    greetings = ["hello", "hi", "hey", "good morning", "good afternoon", "good evening"]
    if any(greet in question_lower for greet in greetings):
        if len(chat_history) <= 1:
            yield "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"
        else:
            yield "Hello! How may I assist you?"
        return    

    # 4️⃣ Normal question handling
    chat_history.append(f"User: {question}")

    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2

    answer_collected = ""  # To store the full answer

    try:
        for token in agent_answer(question):
            yield token
            answer_collected += token
    except Exception as e:
        yield f"\n\n❌ Error occurred while generating the answer: {str(e)}"
        return

    chat_history.append(f"Assistant: {answer_collected}")
    chat_history = chat_history[-max_entries:]

    # 5️⃣ Logging
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
        answer_collected.replace('"', '""'),
        "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))

    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

