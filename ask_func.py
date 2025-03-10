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
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)
    return text.strip()

def clean_repeated_phrases(text):
    return re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)

tool_cache = {}
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []

TABLES = """
1) "Al-Bujairy Terrace Footfalls.xlsx", columns: Date(datetime64[ns]), Footfalls(int64)
2) "Al-Turaif Footfalls.xlsx", columns: Date(datetime64[ns]), Footfalls(int64)
3) "Complaints.xlsx", ...
...
"""

SAMPLE_TEXT = "(omitted for brevity)"
SCHEMA_TEXT = "(omitted for brevity)"

def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "choices" in data and data["choices"]:
        return data["choices"][0]["message"]["content"]
    return ""

def split_question_into_subquestions(user_question):
    ...
    # (same as your existing code)

def is_text_relevant(question, snippet):
    ...
    # (same as your existing code)

def references_tabular_data(question, tables_text):
    ...
    # (same as your existing code, including simple keyword fallback)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    ...
    # (same as your existing code)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    ...
    # (same as your existing code)

def execute_generated_code(code_str):
    ...
    # (same as your existing code)

def tool_3_llm_fallback(user_question):
    ...
    # (same as your existing code)

def final_answer_llm(user_question, index_dict, python_dict):
    ...
    # (same as your existing code)

def post_process_source(final_text, index_dict, python_dict):
    ...
    # (same as your existing code)

def agent_answer(user_question):
    """
    The main function that decides how to respond:
      1) If user input is a pure greeting, return greeting immediately (no fallback).
      2) Otherwise, proceed with Python data + Index data + final LLM, plus source attachment.
    """

    # 1) Check for greeting
    def is_entirely_greeting(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening",
            "assalam", "salam", "hola", "greetings", "howdy", "yo"
        }
        tokens = re.findall(r"[a-z]+", phrase.lower())
        if not tokens:
            return False
        return all(t in greet_words for t in tokens)

    q_stripped = user_question.strip()
    if is_entirely_greeting(q_stripped):
        # If the chat is short, provide the big greeting, else shorter
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

    # 2) If not greeting, check cache or proceed with the normal pipeline
    cache_key = q_stripped.lower()
    if cache_key in tool_cache:
        return tool_cache[cache_key][2]

    # tool_2_code_run
    python_dict = tool_2_code_run(user_question)
    # tool_1_index_search
    index_dict = tool_1_index_search(user_question)

    # final LLM
    final_text = final_answer_llm(user_question, index_dict, python_dict)
    final_text = clean_repeated_phrases(final_text)

    # possibly attach code or index snippet
    final_answer = post_process_source(final_text, index_dict, python_dict)
    tool_cache[cache_key] = (index_dict, python_dict, final_answer)
    return final_answer

def Ask_Question(question):
    """
    The top-level function for handling user input:
    - If user says 'export', we handle that
    - If user says 'restart chat', we do that
    - Otherwise we call agent_answer
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

    # Normal Q&A
    chat_history.append(f"User: {question}")
    answer_text = agent_answer(question)
    chat_history.append(f"Assistant: {answer_text}")

    # Shorten chat_history
    if len(chat_history) > 12:
        chat_history = chat_history[-12:]

    # Logging to Azure Blob (same as your existing code)
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
