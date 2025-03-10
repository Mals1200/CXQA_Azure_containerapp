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
import difflib
import string

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

    # If no direct keyword, do an LLM-based check
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

        # Replace read calls with dataframes.get(...)
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

    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback = tool_3_llm_fallback(user_question)
        return f"{fallback}\nSource: Ai Generated"

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

    to_append = []
    if "source: index & python" in txt_lower:
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
        final_text += "\n\n---SOURCE_DETAILS---\n" + "\n".join(to_append)
        return final_text

import string

def agent_answer(user_question):
    """
    Orchestrate the final answer. 
    - Handle greetings and empty questions upfront
    - Proceed to tools only for valid data queries
    """
    # Handle empty question
    stripped_question = user_question.strip()
    if not stripped_question:
        return "Please provide a question."

    # Enhanced greeting check (removes punctuation & spaces, checks against expanded list)
    translator = str.maketrans('', '', string.punctuation + ' ')
    q_clean = stripped_question.translate(translator).lower()

    greet_words = {
        "hello", "hi", "hey", "morning", "evening", "goodmorning", "goodevening",
        "assalam", "hayo", "hola", "salam", "alsalam", "alsalamualaikum", "assalamualaikum",
        "greetings", "howdy", "whatsup", "sup", "namaste", "shalom", "bonjour", "ciao",
        "konichiwa", "nihao", "marhaba", "ahlan", "sawubona", "hallo", "salut", "holaamigo",
        "heythere", "goodday", "goodafternoon", "yo"
    }

    if q_clean in greet_words:
        if len(chat_history) < 4:
            return (
                "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n"
                "- To reset the conversation type 'restart chat'.\n"
                "- To generate Slides, Charts or Document, type 'export followed by your requirements."
            )
        else:
            return (
                "Hello! How may I assist you?\n"
                "-To reset the conversation type 'restart chat'.\n"
                "-To generate Slides, Charts or Document, type 'export followed by your requirements."
            )

    # Check cache before proceeding to tools
    cache_key = stripped_question.lower()
    if cache_key in tool_cache:
        return tool_cache[cache_key][2]

    # Proceed with data tools only for valid questions
    python_dict = tool_2_code_run(stripped_question)
    index_dict = tool_1_index_search(stripped_question)

    final_text = final_answer_llm(stripped_question, index_dict, python_dict)
    final_text = clean_repeated_phrases(final_text)
    final_answer = post_process_source(final_text, index_dict, python_dict)

    tool_cache[cache_key] = (index_dict, python_dict, final_answer)
    return final_answer



def Ask_Question(question):
    """
    Top-level function:
    - If "export", do export (Call_Export from Export_Agent.py)
    - If "restart chat", clear
    - Otherwise, normal Q&A logic
    """
    global chat_history
    q_lower = question.lower().strip()

    # 1) Handle export requests
    from Export_Agent import Call_Export
    if q_lower.startswith("export"):
        instructions = question[6:].strip()  # everything after "export"

        # Handle cases where chat_history is too short
        latest_question = chat_history[-1] if len(chat_history) >= 1 else "No previous question available."
        latest_answer = chat_history[-2] if len(chat_history) >= 2 else "No previous answer available."

        export_result = Call_Export(latest_question, latest_answer, chat_history, instructions)
        yield export_result
        return

    # 2) Handle "restart chat"
    if q_lower == "restart chat":
        chat_history = []
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # 3) Normal Q&A
    chat_history.append(f"User: {question}")
    answer_text = agent_answer(question)
    chat_history.append(f"Assistant: {answer_text}")

    # Keep chat_history from growing infinitely
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



