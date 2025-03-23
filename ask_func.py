# new v 18 replacement:

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
from tenacity import retry, stop_after_attempt, wait_fixed
from functools import lru_cache
import difflib

#######################################################################################
#                               GLOBAL CONFIG / CONSTANTS
#######################################################################################
CONFIG = {
    "LLM_ENDPOINT": "https://cxqaazureaihub2358016269.openai.azure.com/openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview",
    "LLM_API_KEY": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor",
    "SEARCH_SERVICE_NAME": "cxqa-azureai-search",
    "SEARCH_ENDPOINT": "https://cxqa-azureai-search.search.windows.net",
    "ADMIN_API_KEY": "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv",
    "INDEX_NAME": "vector-1741865904949",
    "SEMANTIC_CONFIG_NAME": "vector-1741865904949-semantic-configuration",
    "CONTENT_FIELD": "chunk",
    "ACCOUNT_URL": "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "SAS_TOKEN": "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",
    "CONTAINER_NAME": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore",
    "TARGET_FOLDER_PATH": "UI/2024-11-20_142337_UTC/cxqa_data/tabular/"
}

logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []
tool_cache = {}

#######################################################################################
#                           TABLES / SCHEMA (constants as strings)
#######################################################################################
TABLES = """
[Tables structure same as original]
"""

SAMPLE_TEXT = """
[Sample text same as original]
"""

SCHEMA_TEXT = """
[Schema text same as original]
"""

#######################################################################################
#                   CENTRALIZED LLM CALL
#######################################################################################
def call_llm(system_prompt, user_prompt, max_tokens=500, temperature=0.0):
    try:
        headers = {"Content-Type": "application/json", "api-key": CONFIG["LLM_API_KEY"]}
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
#                   COMBINED TEXT CLEANING
#######################################################################################
def clean_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)
    return text.strip()

#######################################################################################
#                              SUBQUESTION SPLITTING
#######################################################################################
def split_question_into_subquestions(user_question, use_semantic_parsing=True):
    if not user_question.strip():
        return []
    if not use_semantic_parsing:
        text = re.sub(r"\s+and\s+", " ~SPLIT~ ", user_question, flags=re.IGNORECASE)
        text = re.sub(r"\s*&\s*", " ~SPLIT~ ", text)
        parts = text.split("~SPLIT~")
        return [p.strip() for p in parts if p.strip()]
    else:
        system_prompt = "You are a helpful assistant. Split the question into subquestions if needed."
        user_prompt = f"Split this question if applicable:\n\n{user_question}"
        answer_text = call_llm(system_prompt, user_prompt, max_tokens=300)
        lines = [line.lstrip("•-0123456789). ").strip() for line in answer_text.split("\n") if line.strip()]
        return [l for l in lines if l] or [user_question]

#######################################################################################
#                 REFERENCES CHECK & RELEVANCE CHECK
#######################################################################################
def references_tabular_data(question, tables_text):
    llm_response = call_llm(
        "You are a YES/NO classifier. Decide if the question needs tabular data.",
        f"Question: {question}\n\nTables: {tables_text}\nReply ONLY 'YES' or 'NO'."
    )
    return "YES" in llm_response.strip().upper()

def is_text_relevant(question, snippet):
    content = call_llm(
        "Classify if the snippet is relevant to the question. Reply 'YES' or 'NO'.",
        f"Question: {question}\nSnippet: {snippet}"
    )
    return content.strip().upper().startswith("YES")

#######################################################################################
#                              TOOL #1 - Index Search
#######################################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    try:
        search_client = SearchClient(
            endpoint=CONFIG["SEARCH_ENDPOINT"],
            index_name=CONFIG["INDEX_NAME"],
            credential=AzureKeyCredential(CONFIG["ADMIN_API_KEY"])
        )
        merged_docs = []
        for subq in split_question_into_subquestions(user_question):
            results = search_client.search(
                search_text=subq,
                query_type="semantic",
                semantic_configuration_name=CONFIG["SEMANTIC_CONFIG_NAME"],
                top=top_k,
                select=["title", CONFIG["CONTENT_FIELD"]]
            )
            for r in results:
                if snippet := r.get(CONFIG["CONTENT_FIELD"], "").strip():
                    merged_docs.append({"title": r.get("title", ""), "snippet": snippet})
        
        relevant_docs = [doc for doc in merged_docs if is_text_relevant(user_question, doc["snippet"])]
        if not relevant_docs:
            return {"top_k": "No information"}
        
        for doc in relevant_docs:
            doc["weight_score"] = sum([
                10 if "policy" in doc["title"].lower() else 0,
                5 if "report" in doc["title"].lower() else 0,
                3 if "sop" in doc["title"].lower() else 0
            ])
        
        docs_sorted = sorted(relevant_docs, key=lambda x: x["weight_score"], reverse=True)[:top_k]
        return {"top_k": "\n\n---\n\n".join([d["snippet"] for d in docs_sorted])}
    except Exception as e:
        logging.error(f"Error in Index Search: {e}")
        return {"top_k": "No information"}

#######################################################################################
#                              TOOL #2 - Code Run
#######################################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(user_question):
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}
    
    code_str = call_llm(
        f"You are a Python expert. Generate code to answer: {user_question}",
        f"Schemas: {SCHEMA_TEXT}\nSamples: {SAMPLE_TEXT}",
        max_tokens=1200
    )
    
    if not code_str or code_str == "404":
        return {"result": "No information", "code": ""}
    
    try:
        blob_service_client = BlobServiceClient(
            account_url=CONFIG["ACCOUNT_URL"],
            credential=CONFIG["SAS_TOKEN"]
        )
        container_client = blob_service_client.get_container_client(CONFIG["CONTAINER_NAME"])
        dataframes = {}
        
        for blob in container_client.list_blobs(name_starts_with=CONFIG["TARGET_FOLDER_PATH"]):
            blob_client = container_client.get_blob_client(blob.name)
            blob_data = blob_client.download_blob().readall()
            file_name = blob.name.split('/')[-1]
            
            if file_name.endswith(('.xlsx', '.xls')):
                dataframes[file_name] = pd.read_excel(io.BytesIO(blob_data))
            elif file_name.endswith('.csv'):
                dataframes[file_name] = pd.read_csv(io.BytesIO(blob_data))
        
        code_modified = code_str.replace("pd.read_excel(", "dataframes.get(")
        output_buffer = StringIO()
        with contextlib.redirect_stdout(output_buffer):
            exec(code_modified, {"dataframes": dataframes, "pd": pd, "datetime": datetime})
        output = output_buffer.getvalue().strip()
        return {"result": output or "No output", "code": code_str}
    except Exception as e:
        return {"result": f"Execution error: {e}", "code": code_str}

#######################################################################################
#                              TOOL #3 - LLM Fallback
#######################################################################################
def tool_3_llm_fallback(user_question):
    return call_llm(
        "You are a helpful AI. Provide a concise answer.",
        user_question,
        max_tokens=500
    )

#######################################################################################
#                            FINAL ANSWER FROM LLM
#######################################################################################
def final_answer_llm(user_question, index_dict, python_dict):
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    if index_top_k == "No information" and python_result == "No information":
        return f"{tool_3_llm_fallback(user_question)}\nSource: AI Generated"
    
    combined_info = f"INDEX_DATA:\n{index_top_k}\nPYTHON_DATA:\n{python_result}"
    return call_llm(
        "Combine information from index and python data to answer the question.",
        f"Question: {user_question}\nData:\n{combined_info}"
    ) + "\nSource: Index & Python" if "No information" not in [index_top_k, python_result] else "\nSource: AI Generated"

#######################################################################################
#                           LOG INTERACTION
#######################################################################################
def Log_Interaction(question, full_answer, chat_history, user_id, index_dict=None, python_dict=None):
    # [Implementation same as original]
    pass

#######################################################################################
#                         GREETING HANDLING + AGENT ANSWER
#######################################################################################
def agent_answer(user_question):
    if not user_question.strip():
        return ""
    
    def is_greeting(phrase):
        greet_words = {"hello", "hi", "hey", "good morning", "good evening"}
        return any(word in phrase.lower() for word in greet_words)
    
    if is_greeting(user_question):
        return "Hello! How can I assist you today? You can ask questions or type 'export' for reports."
    
    cache_key = user_question.lower()
    if cache_key in tool_cache:
        return tool_cache[cache_key][2]
    
    index_dict = tool_1_index_search(user_question)
    python_dict = tool_2_code_run(user_question) if references_tabular_data(user_question, TABLES) else {"result": "No information"}
    raw_answer = final_answer_llm(user_question, index_dict, python_dict)
    final_answer = clean_text(raw_answer)
    
    tool_cache[cache_key] = (index_dict, python_dict, final_answer)
    return final_answer

#######################################################################################
#                            ASK_QUESTION (Main Entry)
#######################################################################################
def Ask_Question(question, user_id="anonymous"):
    global chat_history
    
    question_lower = question.lower().strip()
    if question_lower.startswith("export"):
        from Export_Agent import Call_Export
        return "\n".join(Call_Export(question, chat_history[-1], chat_history, question[6:].strip()))
    
    if question_lower == "restart chat":
        global tool_cache
        chat_history = []
        tool_cache.clear()
        return "Chat restarted."
    
    chat_history.append(f"User: {question}")
    answer = agent_answer(question)
    chat_history.append(f"Assistant: {answer}")
    chat_history = chat_history[-10:]
    
    Log_Interaction(
        question=question,
        full_answer=answer,
        chat_history=chat_history,
        user_id=user_id,
        index_dict=tool_1_index_search(question),
        python_dict=tool_2_code_run(question)
    )
    return answer
