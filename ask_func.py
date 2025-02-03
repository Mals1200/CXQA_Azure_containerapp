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
from dotenv import load_dotenv
from functools import lru_cache

# Suppress Azure SDK's http_logging_policy logs:
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)  # Suppress all Azure logs

chat_history = []  # Define the global variable


# =====================================
# Streaming Helper Function
# =====================================
def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    """
    A helper function to stream the Azure OpenAI response token by token
    and return the concatenated text. It can optionally print tokens as they come in.
    """
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
                                print(content_piece, end="", flush=True)  # Print if enabled
                            final_text += content_piece
                    except json.JSONDecodeError:
                        pass
        if print_stream:
            print()
    return final_text


# =====================================
# Deciding Path (Index or Python)
# =====================================
def Path_LLM(question):
    """
    Determines whether the user's question should be answered using datafiles (Python)
    or a knowledge base (Index).
    """
    import requests
    import json

    # Azure OpenAI Configuration
    LLM_DEPLOYMENT_NAME = "gpt-4o"
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    # Hardcoded Tables (metadata of data available)
    Tables = """
    1) "Al-Bujairy Terrace Footfalls.xlsx" - Contains Date and Footfalls
    2) "Al-Turaif Footfalls.xlsx" - Contains Date and Footfalls
    3) "Complaints.xlsx" - Includes Incident Category, Status, Resolution, etc.
    4) "Duty manager log.xlsx" - Logs issues, department, incidents, remarks, status
    5) "Food and Beverages (F&B) Sales.xlsx" - Restaurant sales and categories
    ...
    """

    prompt = f"""
    You are a decision-making assistant. Based on the available data, determine if the question 
    can be answered using "Python" (data analysis) or "Index" (external knowledge base).
    
    **Rules**:
    - Answer **Python** if the question can be answered using structured data.
    - Otherwise, answer **Index**.
    - If it's a greeting, return **Hello! How may I assist you?**.
    - If it's outside scope, return **This is outside of my scope, may I help you with anything else?**.
    
    User question:
    {question}

    Available Dataframes:
    {Tables}

    Chat_history:
    {chat_history}
    """

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    payload = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": question}
        ],
        "max_tokens": 100,
        "temperature": 0.0,
        "stream": True
    }

    try:
        streamed_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        answer = streamed_answer.strip()
        return answer if answer in ["Python", "Index", "Hello! How may I assist you?", "This is outside of my scope, may I help you with anything else?"] else "Error"
    except Exception as e:
        return f"Error: {str(e)}"


# ==============================================
# Run Path: Either "Index" for search, or "Python" for code execution
# ==============================================
Content = None  # Global variable to store response

def run_path(path: str, question: str = ""):
    """
    Runs the appropriate processing path based on the `path` value.
    """
    global Content

    # -----------------------
    # Index Path (Azure Search)
    # -----------------------
    if path == "Index":
        SEARCH_SERVICE_NAME = "cxqa-azureai-search"
        SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
        INDEX_NAME = "cxqa-ind-v6"
        ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

        try:
            search_client = SearchClient(
                endpoint=SEARCH_ENDPOINT,
                index_name=INDEX_NAME,
                credential=AzureKeyCredential(ADMIN_API_KEY)
            )
        except Exception as e:
            return "Error connecting to Azure Search."

        def perform_search(query: str, top: int = 5):
            """Performs a search query against the Azure Cognitive Search index."""
            try:
                results = search_client.search(
                    search_text=query,
                    query_type="semantic",
                    semantic_configuration_name="azureml-default",
                    top=top,
                    include_total_count=False
                )
                return [res["content"] for res in results]
            except Exception:
                return []

        results = perform_search(question, top=4)
        retrieved_info_str = "\n".join(results)

        return f"Answer: {retrieved_info_str}\n\nSource: Index"

    # -----------------------
    # Python Path (Data Analysis)
    # -----------------------
    elif path == "Python":
        def Execute(code_str: str) -> str:
            """Executes Python code for data processing and returns the result."""
            try:
                output_buffer = io.StringIO()
                with contextlib.redirect_stdout(output_buffer):
                    local_vars = {}
                    exec(code_str, {}, local_vars)
                return output_buffer.getvalue().strip() or "Execution completed with no output."
            except Exception as e:
                return f"An error occurred during execution: {e}"

        The_Code = 'print("Sample Python Execution")'  # Placeholder for LLM-generated code
        exec_result = Execute(The_Code)
        return f"{exec_result}\n\nSource: Python"

    return "Invalid Path"


# ==============================================
# Main Function to Ask a Question
# ==============================================
def Ask_Question(question):
    """Handles incoming user questions and decides processing path."""
    global chat_history
    
    chat_history.append(f"User: {question}")
    max_entries = 10  # Keep last 10 interactions

    path_decision = Path_LLM(question)
    answer = run_path(path_decision, question)

    chat_history.append(f"Assistant: {answer}")
    chat_history = chat_history[-max_entries:]  # Trim chat history

    return f"User: {question}\nAssistant: {answer}"
