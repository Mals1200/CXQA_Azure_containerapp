# ask_func.py

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

# <-- Add this import for your PPT_Agent:
from PPT_Agent import Call_PPT

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

    # Fake endpoint & key just for structure
    LLM_ENDPOINT = (
        "https://your-azure-openai-endpoint.openai.azure.com/"
        "openai/deployments/fake_model/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_API_KEY"

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

    # For demonstration, always return True (or do real call if you have actual keys)
    return True

def references_tabular_data(question, tables_text):
    # Fake endpoint & key just for structure
    LLM_ENDPOINT = (
        "https://your-azure-openai-endpoint.openai.azure.com/"
        "openai/deployments/fake_model/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "FAKE_API_KEY"

    llm_system_message = (
        "You are a helpful agent. Decide if the user's question references or requires the tabular data.\n"
        "Return ONLY 'YES' or 'NO' (in all caps)."
    )
    llm_user_message = f"""
    User question: {question}

    We have these tables: {tables_text}

    Does the user need the data from these tables to answer their question?
    The tables are not exclusive to the data it has, just a sample. Return ONLY 'YES' or 'NO'.
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

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    # For demonstration, always return True (or do real call if you have actual keys)
    return True

def tool_1_index_search(user_question, top_k=5):
    # Fake search credentials
    SEARCH_SERVICE_NAME = "fake-search-service"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    INDEX_NAME = "fake-index"
    ADMIN_API_KEY = "FAKE_SEARCH_ADMIN_KEY"

    subquestions = split_question_into_subquestions(user_question)

    # For demonstration, we skip the real call
    # We'll pretend we got some relevant text from an index
    example_snippet = "Some relevant snippet from the index."
    return {"top_k": example_snippet}

def tool_2_code_run(user_question):
    # Check if the question references tabular data
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    # We'll skip the real call to LLM to get code
    # and pretend we got code (and ran it).
    # Return some fake results for demonstration.
    sample_code = """print("This is a fake Python code result for demonstration.")"""
    exec_result = execute_generated_code(sample_code)

    return {"result": exec_result, "code": sample_code}

def execute_generated_code(code_str):
    # Instead of real code execution, just do a quick check
    output_buffer = StringIO()
    with contextlib.redirect_stdout(output_buffer):
        # Simulate the code printing something
        print("FAKE Execution: Hello from code!")
    return output_buffer.getvalue().strip()

def tool_3_llm_fallback(user_question):
    # In a real scenario, you'd call your Azure OpenAI fallback
    return "Fallback answer from general knowledge."

def final_answer_llm(user_question, index_dict, python_dict):
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    # If we have no info from index or python, do fallback
    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        return f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"

    # Otherwise, let's pretend we used them to craft an answer
    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"
    # We'll skip an actual LLM call and just craft a sample answer:
    final_answer = f"Based on the index data and python data:\n\nIndex says:\n{index_top_k}\n\nPython says:\n{python_result}\n\nSource: Index & Python"
    return final_answer

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
        # If there's no recognized source line, just return final_text
        return final_text

def agent_answer(user_question):
    # If question is empty at first usage
    if user_question.strip() == "" and len(chat_history) < 2:
        return ""

    # Check for greetings
    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening", "goodmorning", "good morning",
            "goodevening", "good evening", "assalam", "hayo", "hola", "salam", "alsalam",
            "alsalamualaikum", "greetings", "howdy", "what's up", "yo", "sup", "namaste",
            "shalom", "bonjour", "ciao", "konichiwa","ni hao", "marhaba", "ahlan",
            "sawubona", "hallo", "salut", "hola amigo", "hey there", "good day"
        }
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
            return "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"
        else:
            return "Hello! How may I assist you?"

    # Normal logic
    index_dict = tool_1_index_search(user_question)
    python_dict = tool_2_code_run(user_question)
    final_ans = final_answer_llm(user_question, index_dict, python_dict)
    final_ans_with_src = post_process_source(final_ans, index_dict, python_dict)
    return final_ans_with_src

def Ask_Question(question):
    global chat_history

    chat_history.append(f"User: {question}")

    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2

    # Generate normal answer
    answer = agent_answer(question)

    ###################################
    # Check if user wants "export ppt"
    ###################################
    question_lower = question.strip().lower()
    if question_lower.startswith("export ppt"):
        # remove "export ppt" from the question
        possible_instructions = question[10:].strip()
        if not possible_instructions:
            possible_instructions = "No instructions provided"

        # Call PPT
        ppt_result = Call_PPT(
            latest_question=question,
            latest_answer=answer,
            chat_history=chat_history,
            instructions=possible_instructions
        )
        final_answer = ppt_result
    else:
        final_answer = answer

    chat_history.append(f"Assistant: {final_answer}")
    chat_history = chat_history[-max_entries:]  # keep chat short

    # Logging to Blob (fake placeholders)
    account_url = "https://your-blob-storage-account.blob.core.windows.net"
    sas_token = "FAKE_SAS_TOKEN"
    container_name = "fake-container"

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
        final_answer.replace('"','""'),
        "anonymous"
    ]
    lines.append(",".join(f'"{x}"' for x in row))

    new_csv_content = "\n".join(lines) + "\n"
    # In a real scenario, you'd upload to the blob:
    # blob_client.upload_blob(new_csv_content, overwrite=True)

    return final_answer
