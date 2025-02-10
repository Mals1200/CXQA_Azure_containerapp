import os
import io
import json
import logging
import requests
import contextlib
import pandas as pd
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from functools import lru_cache

# Suppress Azure SDK logs:
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = []  # Global chat history

# =====================================
# Streaming Helper Function
# =====================================
def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    """
    Streams the Azure OpenAI response token by token and returns the concatenated text.
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
                        if ("choices" in data_json and data_json["choices"] and "delta" in data_json["choices"][0]):
                            content_piece = data_json["choices"][0]["delta"].get("content", "")
                            if print_stream:
                                print(content_piece, end="", flush=True)
                            final_text += content_piece
                    except json.JSONDecodeError:
                        pass
        if print_stream:
            print()
    return final_text

# =====================================
# Decide which path to take (Index or Python)
# =====================================
def Path_LLM(question):
    """
    Uses Azure OpenAI to decide which processing path to take.
    Returns exactly one of:
      - "Python" if the user's question can be answered using the provided data files.
      - "Index" if the question does not match any dataset.
      - "Hello! How may I assist you?" if the input is a greeting.
      - "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?" if the question is empty (and chat history is empty).
    """
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    Tables = """
1) "Al-Bujairy Terrace Footfalls.xlsx": {Date: datetime64[ns], Footfalls: int64}
2) "Al-Turaif Footfalls.xlsx": {Date: datetime64[ns], Footfalls: int64}
3) "Complaints.xlsx": {Created On: datetime64[ns], Incident Category: object, Status: object, Resolved On Date(Local): object, Incident Description: object, Resolution: object}
4) "Duty manager log.xlsx": {DM NAME: object, Date: datetime64[ns], Shift: object, Issue: object, Department: object, Team: object, Incident: object, Remark: object, Status: object, ETA: object, Days: float64}
5) "Food and Beverages (F&b) Sales.xlsx": {Restaurant name: object, Category: object, Date: datetime64[ns], Covers: float64, Gross Sales: float64}
6) "Meta-Data.xlsx": {Visitation: object, Attendance: object, Visitors: object, Guests: object, Footfalls: object, Unnamed: 5: object}
7) "PE Observations.xlsx": {Unnamed: 0: object, Unnamed: 1: object}
8) "Parking.xlsx": {Date: datetime64[ns], Valet Volume: int64, Valet Revenue: int64, Valet Utilization: float64, BCP Revenue: object, BCP Volume: int64, BCP Utilization: float64, SCP Volume: int64, SCP Revenue: int64, SCP Utilization: float64}
9) "Qualitative Comments.xlsx": {Open Ended: object}
10) "Tenants Violations.xlsx": {Unnamed: 0: object, Unnamed: 1: object}
11) "Tickets.xlsx": {Date: datetime64[ns], Number of tickets: int64, revenue: int64, attendnace: int64, Reservation Attendnace: int64, Pass Attendance: int64, Male attendance: int64, Female attendance: int64, Rebate value: float64, AM Tickets: int64, PM Tickets: int64, Free tickets: int64, Paid tickets: int64, Free tickets %: float64, Paid tickets %: float64, AM Tickets %: float64, PM Tickets %: float64, Rebate Rate V 55: float64, Revenue  v2: int64}
12) "Top2Box Summary.xlsx": {Month: datetime64[ns], Type: object, Top2Box scores/ rating: float64}
13) "Total Landscape areas and quantities.xlsx": {Assets: object, Unnamed: 1: object, Unnamed: 2: object, Unnamed: 3: object}
    """

    # --- Modified prompt with explicit instructions ---
    prompt = f"""
You are a decision-making assistant. You have access to a list of data files with their columns.

**Instructions:**
Return EXACTLY ONE of these responses (nothing else):
  - "Python" if the user's question can be answered using the provided data files.
  - "Index" if the question does not match any dataset.
  - "Hello! How may I assist you?" if the user's input is a greeting.
  - "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?" if the question is empty and chat history is empty.

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
        "max_tokens": 20,
        "temperature": 0.0,
        "stream": True
    }

    try:
        streamed_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        raw_answer = streamed_answer.strip()
        # Debug print to see exactly what the LLM returned:
        print("DEBUG - LLM raw response:", repr(raw_answer))
        # Normalize the response (ignore case and extra spaces)
        answer_lower = raw_answer.lower()
        if answer_lower == "python":
            answer = "Python"
        elif answer_lower == "index":
            answer = "Index"
        elif answer_lower == "hello! how may i assist you?":
            answer = "Hello! How may I assist you?"
        elif answer_lower == "hello! i'm the cxqa ai assistant. i'm here to help you. what would you like to know today?":
            answer = "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"
        else:
            # If the response is not one of the expected ones, log it and return "Error"
            print("DEBUG - Unexpected LLM response. Using fallback 'Error'.")
            answer = "Error"
        return answer
    except Exception as e:
        return f"Error: {str(e)}"

# =====================================
# Process question via Index or Python path
# =====================================
Content = None

def run_path(path: str, question: str = ""):
    """
    Based on the decision in Path_LLM, runs either the "Index" or "Python" path.
    """
    global Content

    def Index_LLM(question, search_results):
        LLM_ENDPOINT = (
            "https://cxqaazureaihub2358016269.openai.azure.com/"
            "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
        )
        LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

        prompt = f"""
You are a helpful assistant that answers the user’s question **only using the provided indexed information**.
If the answer is not available, reply with:
"No information was found in the Data. Can I help you with anything else?"

User question:
{question}

Indexed Information:
{search_results}

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
            "max_tokens": 1000,
            "temperature": 0.7,
            "stream": True
        }
        try:
            streamed_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
            return streamed_answer
        except requests.exceptions.HTTPError:
            return "An error occurred while processing your request."
        except Exception:
            return "An unexpected error occurred."

    if path == "Index":
        # Setup for the index search
        SEARCH_SERVICE_NAME = "cxqa-azureai-search"
        SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
        INDEX_NAME = "cxqa-ind-v6"
        ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

        try:
            from azure.search.documents import SearchClient
            search_client = SearchClient(
                endpoint=SEARCH_ENDPOINT,
                index_name=INDEX_NAME,
                credential=AzureKeyCredential(ADMIN_API_KEY)
            )
        except Exception as e:
            raise Exception(f"Search client setup error: {str(e)}")

        def perform_search(query: str, top: int = 5):
            try:
                results = search_client.search(
                    search_text=query,
                    query_type="semantic",
                    semantic_configuration_name="azureml-default",
                    top=top,
                    include_total_count=False
                )
                search_results = []
                for result in results:
                    content = result.get("content", "")
                    search_results.append(content)
                return search_results
            except Exception:
                return []

        results = perform_search(question, top=4)
        retrieved_info_str = "\n\n---\n\n".join(str(item) for item in results)
        final_answer = Index_LLM(question, retrieved_info_str)
        Content = final_answer
        return f"{Content}\n\nSource: Index.\nThe Documents:\n\n{retrieved_info_str}"

    elif path == "Python":
        def Generate_Code(user_question):
            LLM_ENDPOINT = (
                "https://cxqaazureaihub2358016269.openai.azure.com/"
                "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
            )
            LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

            schema = """
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
            sample = """
Al-Bujairy Terrace Footfalls.xlsx: [{'Date': "Timestamp('2023-01-01 00:00:00')", 'Footfalls': 2950}, ...]
"""
            system_prompt = f"""
You are a python expert. Use the user question along with Chat_history to generate the Python code that will answer the question using the provided dataframe schemas and samples.
Only provide the pure Python code (including necessary imports). Do not include any explanations or quotation marks around the code.
If you cannot provide valid code, reply with "404".

User question:
{user_question}

Dataframes schemas:
{schema}

Dataframes samples:
{sample}

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
                "temperature": 0.7,
                "stream": True
            }
            try:
                code_streamed = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
                code_result = code_streamed.strip()
                if "404" in code_result or not code_result:
                    return "404"
                return code_result
            except Exception as ex:
                return f"Error: {str(ex)}"

        def Execute(code_str: str) -> str:
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
                # Replace file reads in the generated code with our dataframes
                code_modified = code_str.replace("pd.read_excel(", "dataframes.get(")
                code_modified = code_modified.replace("pd.read_csv(", "dataframes.get(")
                output_buffer = io.StringIO()
                with contextlib.redirect_stdout(output_buffer):
                    local_vars = {"dataframes": dataframes, "pd": pd, "datetime": datetime}
                    exec(code_modified, {}, local_vars)
                output = output_buffer.getvalue().strip()
                return output if output else "Execution completed with no output."
            except Exception as e:
                return f"An error occurred during code execution: {e}"

        The_Code = Generate_Code(question)
        if The_Code == "404" or The_Code.startswith("Error"):
            Content = "404"
            print("DEBUG - Code generation returned 404 or error.")
        else:
            exec_result = Execute(The_Code)
            Content = exec_result
        return f"{Content}\n\nSource: Python.\nThe code:\n\n{The_Code}"
    else:
        return path

# =====================================
# Main function to answer the question
# =====================================
def Ask_Question(question):
    global chat_history
    chat_history.append(f"User: {question}")
    number_of_messages = 10
    max_pairs = number_of_messages // 2
    max_entries = max_pairs * 2
    path_decision = Path_LLM(question)
    answer = run_path(path_decision, question)
    chat_history.append(f"Assistant: {answer}")
    chat_history = chat_history[-max_entries:]
    return answer
