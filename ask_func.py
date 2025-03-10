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
Al-Bujairy Terrace Footfalls.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Footfalls': 2950}, ...],
Al-Turaif Footfalls.xlsx: [{'Date': "Timestamp('2023-06-01 00:00:00')", 'Footfalls': 694}, ...],
...
"""
SCHEMA_TEXT = """
Al-Bujairy Terrace Footfalls.xlsx: {'Date': 'datetime64[ns]', 'Footfalls': 'int64'},
Al-Turaif Footfalls.xlsx: {'Date': 'datetime64[ns]', 'Footfalls': 'int64'},
Complaints.xlsx: {...},
Duty manager log.xlsx: {...},
Food and Beverages (F&b) Sales.xlsx: {...},
Meta-Data.xlsx: {...},
PE Observations.xlsx: {...},
Parking.xlsx: {...},
Qualitative Comments.xlsx: {...},
Tenants Violations.xlsx: {...},
Tickets.xlsx: {...},
Top2Box Summary.xlsx: {...},
Total Landscape areas and quantities.xlsx: {...},
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
    # ... (unchanged)
    ...

def references_tabular_data(question, tables_text):
    # ... (unchanged)
    ...

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    # ... (unchanged)
    ...

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    # ... (unchanged)
    ...

def execute_generated_code(code_str):
    # ... (unchanged)
    ...

def tool_3_llm_fallback(user_question):
    # ... (unchanged)
    ...

def final_answer_llm(user_question, index_dict, python_dict):
    # ... (unchanged)
    ...

def post_process_source(final_text, index_dict, python_dict):
    # ... (unchanged)
    ...

####################################################
#       REMOVED GREETING LOGIC FROM HERE           #
####################################################
def agent_answer(user_question):
    # The greeting-check code was removed from this function to avoid duplication.

    user_question_stripped = user_question.strip()

    # 1) Check cache
    cache_key = user_question_stripped.lower()
    if cache_key in tool_cache:
        _, _, cached_answer = tool_cache[cache_key]
        yield cached_answer
        return

    # 2) Decide if we need Python data
    needs_tabular_data = references_tabular_data(user_question, TABLES)
    index_dict = {"top_k": "No information"}
    python_dict = {"result": "No information", "code": ""}

    # 3) If needed, run the python tool
    if needs_tabular_data:
        python_dict = tool_2_code_run(user_question)

    # 4) Always run index search
    index_dict = tool_1_index_search(user_question)

    # 5) Gather final answer
    full_answer = ""
    for token in final_answer_llm(user_question, index_dict, python_dict):
        print(token, end='', flush=True)  # Optional: stream to console
        yield token
        full_answer += token

    # 6) Clean repeated phrases
    full_answer = clean_repeated_phrases(full_answer)

    # 7) Post-process to add code or snippet
    final_answer_with_source = post_process_source(full_answer, index_dict, python_dict)
    tool_cache[cache_key] = (index_dict, python_dict, final_answer_with_source)

    extra_part = final_answer_with_source[len(full_answer):]
    if extra_part.strip():
        yield "\n\n" + extra_part

####################################################
#      GREETING LOGIC CONSOLIDATED IN THIS FUNC    #
####################################################
def Ask_Question(question):
    global chat_history
    question_lower = question.lower().strip()

    # Full greeting set:
    greet_words = {
        "hello", "hi", "hey", "morning", "evening", "goodmorning", "good morning", "Good morning",
        "goodevening", "good evening", "assalam", "hayo", "hola", "salam", "alsalam",
        "alsalamualaikum", "alsalam", "salam", "al salam", "assalamualaikum",
        "greetings", "howdy", "what's up", "yo", "sup", "namaste", "shalom", "bonjour", "ciao",
        "konichiwa", "ni hao", "marhaba", "ahlan", "sawubona", "hallo", "salut", "hola amigo",
        "hey there", "good day"
    }

    # 1) Export logic
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
        return

    # 2) Restart logic
    if question_lower == "restart chat":
        chat_history = []
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    # 3) Greet check in one place
    if any(greet in question_lower for greet in greet_words):
        if len(chat_history) < 4:
            yield ("Hello! I'm The CXQA AI Assistant. I'm here to help you. "
                   "What would you like to know today?\n"
                   "- To reset the conversation type 'restart chat'.\n"
                   "- To generate Slides, Charts or Document, type 'export' followed by your requirements.")
        else:
            yield ("Hello! How may I assist you?\n"
                   "-To reset the conversation type 'restart chat'.\n"
                   "-To generate Slides, Charts or Document, type 'export' followed by your requirements.")
        return

    # 4) Normal question
    chat_history.append(f"User: {question}")

    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2

    answer_collected = ""
    try:
        for token in agent_answer(question):
            yield token
            answer_collected += token
    except Exception as e:
        yield f"\n\n❌ Error occurred while generating the answer: {str(e)}"
        return

    chat_history.append(f"Assistant: {answer_collected}")
    chat_history = chat_history[-max_entries:]

    # 5) Logging
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
