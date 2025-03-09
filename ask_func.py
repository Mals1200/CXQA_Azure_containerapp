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
from tenacity import retry, stop_after_attempt, wait_fixed
from functools import lru_cache
import difflib

def clean_repeated_patterns(text):
    # Remove repeated words like: "TheThe", "total total"
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)
    
    # Remove repeated characters within a word: e.g., "footfallsfalls"
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', flags=re.IGNORECASE)

    # Remove excessive punctuation or spaces
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)
    
    return text.strip()

def is_repeated_phrase(last_text, new_text, threshold=0.98):
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
Complaints.xlsx: [{'Created On': "Timestamp('2024-01-01 00:00:00')", ...}],
Duty manager log.xlsx: [{'DM NAME': 'Abdulrahman Alkanhal', 'Date': "Timestamp('2024-06-01 00:00:00')", ...}],
Food and Beverages (F&b) Sales.xlsx: [{'Restaurant name': 'Angelina', 'Category': 'Casual Dining', ...}],
Meta-Data.xlsx: [{'Visitation': 'Revenue', 'Attendance': 'Income', 'Visitors': 'Sales', 'Guests': 'Gross Sales', ...}],
PE Observations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...],
Parking.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Valet Volume': 194, 'Valet Revenue': 29100, ...}],
Qualitative Comments.xlsx: [{'Open Ended': 'يفوقو توقعاتي كل شيء رائع'}, ...],
Tenants Violations.xlsx: [{'Unnamed: 0': nan, 'Unnamed: 1': nan}, ...],
Tickets.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Number of tickets': 4644, ...}],
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

import json
import requests

def split_question_into_subquestions(user_question):
    """
    Uses an LLM to determine if the question should be split into multiple sub-questions.
    Returns a list of sub-questions if needed, otherwise returns the question as a single item list.
    """
    LLM_ENDPOINT = (
        "https://fake-azure-openai-resource.openai.azure.com/"
        "openai/deployments/fake-model/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "fake-api-key"

    system_prompt = """
    You are an expert in semantic parsing. Your task is to carefully split complex questions into their most meaningful sub-questions.
    
    **Rules:**
    1. Split **only** if the question has distinct, meaningful sub-questions.
    2. Do **NOT** split phrases like "rainy and cloudy" which belong together.
    3. Return a **valid JSON array of strings**, where each string is a well-formed sub-question.
    4. If no splitting is necessary, return the original question as a **single-item list**.
    """

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Question: {user_question}\n\nReturn the JSON array of sub-questions."}
        ],
        "max_tokens": 500,
        "temperature": 0,
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    # For demonstration, just return the question unchanged
    # Real code would make the request to LLM_ENDPOINT
    return [user_question]

def is_text_relevant(question, snippet):
    # Dummy function to "simulate" a relevance check
    # Real code would call the LLM
    if not snippet.strip():
        return False
    # Fake logic: just return True if there's at least one matching word
    q_words = set(question.lower().split())
    s_words = set(snippet.lower().split())
    return len(q_words.intersection(s_words)) > 0

def references_tabular_data(question, tables_text):
    # Dummy function that always returns True if it sees "footfall", "ticket", or "sales" etc.
    # Real code would call an LLM as in your original code
    keywords = ["footfall", "ticket", "sales", "complaint", "parking", "landscape"]
    if any(k in question.lower() for k in keywords):
        return True
    return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    # Fake index search results
    # Real code uses Azure Cognitive Search
    example_snippets = [
        "Snippet 1 about footfalls in Al-Bujairy Terrace",
        "Snippet 2 about ticket revenue on 2023-01-02",
        "Snippet 3 about parking usage and valet volume"
    ]
    subquestions = split_question_into_subquestions(user_question)
    relevant_texts = []
    for snippet in example_snippets:
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

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    # Check if question references tables
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    # Fake "generated code" that calculates something
    code_str = """import pandas as pd
print("Fake result: 123 footfalls total.")
"""
    # We'll execute the code
    execution_result = execute_generated_code(code_str)
    return {"result": execution_result, "code": code_str}

def execute_generated_code(code_str):
    # Fake reading from Azure Blob
    # Real code would read your actual files from blob
    try:
        # We'll just simulate a result
        output_buffer = StringIO()
        with contextlib.redirect_stdout(output_buffer):
            local_vars = {"pd": pd, "datetime": datetime}
            exec(code_str, {}, local_vars)
        output = output_buffer.getvalue().strip()
        return output if output else "Execution completed with no output."
    except Exception as e:
        return f"An error occurred during code execution: {e}"

def tool_3_llm_fallback(user_question):
    # If no info from the index or code, produce a fallback
    return "General answer from fallback. (No relevant data found)"

def final_answer_llm(user_question, index_dict, python_dict):
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    # If no info from both
    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        yield f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"
        return

    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"

    # Fake final answer using both data
    final_answer = "Here is a combined answer using Index & Python data.\n"
    final_answer += "Index says: " + index_top_k + "\n"
    final_answer += "Python says: " + python_result + "\n"
    final_answer += "Source: Index & Python"
    yield final_answer

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
    # If question is empty at first usage
    if user_question.strip() == "" and len(chat_history) < 2:
        yield ""

    # Very basic greeting detection
    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening", "assalam", "salam", "greetings"
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
            yield ("Hello! I'm The CXQA AI Assistant. How can I help you today?\n"
                   "- To reset the conversation type 'restart chat'.\n"
                   "- To generate Slides, Charts or Document, type 'export' followed by your request.")
        else:
            yield "Hello! How may I assist you?\n-To reset: 'restart chat'."
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

    # Always do index search
    index_dict = tool_1_index_search(user_question)

    full_answer = ""

    for token in final_answer_llm(user_question, index_dict, python_dict):
        yield token
        full_answer += token

    full_answer = clean_repeated_phrases(full_answer)
    final_answer_with_source = post_process_source(full_answer, index_dict, python_dict)
    tool_cache[cache_key] = (index_dict, python_dict, final_answer_with_source)

    extra_part = final_answer_with_source[len(full_answer):]
    if extra_part.strip():
        yield "\n\n" + extra_part

def Ask_Question(question):
    global chat_history
    question_lower = question.lower().strip()

    if question_lower.startswith("export"):
        # If you have an Export_Agent, call it. Otherwise, just yield a fake:
        yield "Exporting your content... (Fake response)"
        return

    if question_lower == "restart chat":
        chat_history = []
        tool_cache.clear()
        yield "The chat has been restarted."
        return

    greetings = ["hello", "hi", "hey", "good morning", "good afternoon", "good evening"]
    if any(greet in question_lower for greet in greetings):
        if len(chat_history) <= 1:
            yield "Hello! I'm The CXQA AI Assistant. How can I help you today?"
        else:
            yield "Hello! How may I assist you?"
        return

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

    # Logging (FAKE stub)
    # Real code would upload logs to your Azure Blob
    # For demonstration, do nothing here
