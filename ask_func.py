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
import csv  # for potential CSV-related handling in memory if needed

# Suppress Azure SDK's http_logging_policy logs:
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

# Optionally, suppress all Azure logs at once:
logging.getLogger("azure").setLevel(logging.WARNING)

chat_history = [] # Define the global variable

# =====================================
# Streaming Helper Function
# =====================================
def stream_azure_chat_completion(endpoint, headers, payload, print_stream=False):
    """
    A helper function to stream the Azure OpenAI response token by token
    and return the concatenated text. It can optionally print tokens as they come in
    (when print_stream=True).
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
                        if ("choices" in data_json 
                                and data_json["choices"]
                                and "delta" in data_json["choices"][0]):
                            content_piece = data_json["choices"][0]["delta"].get("content", "")
                            # Only print if print_stream=True
                            if print_stream:
                                print(content_piece, end="", flush=True)
                            final_text += content_piece
                    except json.JSONDecodeError:
                        pass
        # Print a final newline only if print_stream=True
        if print_stream:
            print()
    return final_text


# =====================================
# check question path (Index or Python)
# =====================================
def Path_LLM(question):
    """
    Decides whether the user’s question can be answered using the datafiles
    (answer = "Python") or the knowledge base (answer = "Index").
    If greeted, returns "Hello! How may I assist you?".
    If out of scope, returns "This is outside of my scope, may I help you with anything else?".
    Uses streaming, but does not print to console, returning the final text.
    """

    import requests
    import json

    # Azure OpenAI Configuration - using your fake credentials:
    LLM_DEPLOYMENT_NAME = "gpt-4o-3"
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    # Hardcoded tables
    Tables = """
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
    # Construct the prompt
    prompt = f"""
You are a decision-making assistant. You have access to a list of data files (with their columns) below.

**Rules**:
1. If the user’s question along with the Chat_history can be answered using the listed data files, respond with **"Python"**.
2. If the user’s input is a greeting, respond with **"Hello! How may I assist you?"**.
3. If the answer is a a greeting while the chat_history is empty, respond with: **"Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"** otherwise respond with **"Hello! How may I assist you?"**.
4. If the question **does NOT match any dataset**, respond with **"Index"**.

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

    def stream_azure_chat_completion(endpoint, headers, payload):
        """
        Streams the Azure OpenAI response tokens, returns them as a single string,
        and does not print them to console.
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
                                final_text += content_piece
                        except json.JSONDecodeError:
                            pass
            return final_text

    try:
        streamed_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
        answer = streamed_answer.strip()
        valid_responses = [
            "Python",
            "Index",
            "Hello! How may I assist you?",
            "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?"
        ]
        return answer if answer in valid_responses else "Error(L1)"
    except Exception as e:
        return f"Error: {str(e)}"


# ==============================================
# Run path:
# 1) if index: retrieve info, use llm to answer
# 2) if python: llm generates code, execute
# ==============================================
Content = None  # Global variable that will store the final content or answer

def run_path(path: str, question: str = ""):
    """
    A single function that, based on the 'path' argument,
    runs either the "Index" code or the "Python" code.
    It stores the final result in a global variable called 'Content'.
    """
    global Content  # declare that we want to modify the module-level 'Content'

    # -------------------------------------------------------------------------
    # Define the LLM function used *only* in the Index path:
    # -------------------------------------------------------------------------
    def Index_LLM(question, search_results):
        """
        Takes the user question and the search results from Azure Cognitive Search,
        and uses Azure OpenAI to provide a final answer.

        Prompt:
        You are a helpful assistant that answers the user question only using the provided information.
        If the answer is not available reply with "No Information was Found".
        If a greeting is received, reply back with a greeting and "How may I assist you?".
        """
        LLM_DEPLOYMENT_NAME = "gpt-4o-3"
        LLM_ENDPOINT = (
            "https://cxqaazureaihub2358016269.openai.azure.com/"
            "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
        )
        LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

        prompt = f"""
You are a helpful assistant that answers the user’s question **only using the provided indexed information**.
If the answer is not available, reply with:
**"No information was found in the Data. Can I help you with anything else?"**

**Rules**:
1. Only use the provided information to generate an answer.
2. If the answer is not found in the Index, reply with:
   **"No information was found in the Data. Can I help you with anything else?"**
3. Do not make up an answer; only provide factual responses.

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
            "stream": True  # enable streaming
        }

        try:
            # Stream the answer:
            streamed_answer = stream_azure_chat_completion(LLM_ENDPOINT, headers, payload)
            return streamed_answer
        except requests.exceptions.HTTPError:
            return "An error occurred while processing your request."
        except Exception:
            return "An unexpected error occurred."

    # -------------------------------------------------------------------------
    # INDEX PATH
    # -------------------------------------------------------------------------
    if path == "Index":
        # Additional parameters
        top_k = 4  # for the search below

        # Azure Cognitive Search Client Setup
        SEARCH_SERVICE_NAME = "cxqa-azureai-search"
        SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
        INDEX_NAME = "cxqa-ind-v6"
        ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

        try:
            if not all([SEARCH_SERVICE_NAME, INDEX_NAME, ADMIN_API_KEY]):
                missing = []
                if not SEARCH_SERVICE_NAME:
                    missing.append("SEARCH_SERVICE_NAME")
                if not INDEX_NAME:
                    missing.append("INDEX_NAME")
                if not ADMIN_API_KEY:
                    missing.append("ADMIN_API_KEY")
                raise ValueError(f"Missing environment variables: {', '.join(missing)}")

            search_client = SearchClient(
                endpoint=SEARCH_ENDPOINT,
                index_name=INDEX_NAME,
                credential=AzureKeyCredential(ADMIN_API_KEY)
            )
        except Exception as e:
            raise e

        def perform_search(query: str, top: int = 5):
            """
            Performs a semantic search query against the Azure Cognitive Search index.
            """
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
                    metadata = {k: v for k, v in result.items() if k != "content"}
                    search_results.append({"content": content, "metadata": metadata})
                return search_results
            except Exception:
                return []

        results = perform_search(question, top=top_k)

        # Separate metadata and data
        ind_data = []
        for result in results:
            ind_data.append(result["content"])

        # The final content is the joined index results
        retrieved_info_str = "\n\n---\n\n".join(str(item) for item in ind_data)
        final_answer = Index_LLM(question, retrieved_info_str)
        Content = final_answer
        return  f"{Content}\n\nSource: Index.\nThe Documents:\n\n{retrieved_info_str}"

    # -------------------------------------------------------------------------
    # PYTHON PATH
    # -------------------------------------------------------------------------
    elif path == "Python":
        def Generate_Code(user_question):
            """
            Generates Python code to answer the user's question based on provided data schemas and samples
            using Azure OpenAI.
            """
            LLM_DEPLOYMENT_NAME = "gpt-4o-3"
            LLM_ENDPOINT = (
                "https://cxqaazureaihub2358016269.openai.azure.com/"
                "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
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

            system_prompt = f"""
You are a python expert. Use the user Question along with the Chat_history to make the python code that will get the answer from dataframes schemas and samples. 
Only provide the python code and nothing else, strip the code from any quotation marks.
Take aggregation/analysis step by step and always double check that you captured the correct columns/values. 
Don't give examples, only provide the actual code. If you can't provide the code, say "404" and make sure it's a string.

**Rules**:
1. Only use tables columns that exist, and do not makeup anything. 
2. Only return pure Python code that is functional and ready to be executed, and including the imports.
3. Always make code That returns a print statement that answers the question.

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
                "stream": True  # enable streaming
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

                # Replace file reading in the code with usage of dataframes
                code_modified = code_str.replace("pd.read_excel(", "dataframes.get(")
                code_modified = code_modified.replace("pd.read_csv(", "dataframes.get(")

                output_buffer = io.StringIO()
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

        The_Code = Generate_Code(question)
        if The_Code == "404" or The_Code.startswith("Error(L1)"):
            Content = "404"
            print("404")
        else:
            exec_result = Execute(The_Code)
            Content = exec_result

        return f"{Content}\n\nSource: Python.\nThe code:\n\n{The_Code}"

    # -------------------------------------------------------------------------
    # Invalid Path
    # -------------------------------------------------------------------------
    else:
        return path


# ==============================
# Run the full code:
# ==============================
def Ask_Question(question):
    global chat_history

    # 1) Append user's question
    chat_history.append(f"User: {question}")

    # 2) Calculate pairs PROPERLY
    number_of_messages = 10  # total messages (user & assistant)
    max_pairs = number_of_messages // 2  # pairs to retain
    max_entries = max_pairs * 2

    # 3) Generate answer
    path_decision = Path_LLM(question)
    answer = run_path(path_decision, question)

    # 4) Append assistant's answer
    chat_history.append(f"Assistant: {answer}")

    # 5) FINAL truncation (after both messages are added)
    chat_history = chat_history[-max_entries:]

    # Prepare final answer for return
    Answer = f"{answer}"

    # -------------------------------------------------------------------------
    # LOGGING: Save question & answer in a CSV file in Azure Blob (daily file)
    # -------------------------------------------------------------------------

    # Set up the Blob Service Client for the same container used for data
    account_url = "https://cxqaazureaihub8779474245.blob.core.windows.net"
    sas_token = (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    )
    container_name = "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
    container_client = blob_service_client.get_container_client(container_name)

    # We'll store logs in the same folder as tabular data (you can adjust as needed)
    target_folder_path = "UI/2024-11-20_142337_UTC/cxqa_data/logs/"

    # Create a daily filename
    date_str = datetime.now().strftime("%Y_%m_%d")
    log_filename = f"logs_{date_str}.csv"
    blob_name = target_folder_path + log_filename
    blob_client = container_client.get_blob_client(blob_name)

    # 1) Download the existing CSV if it exists
    try:
        existing_blob_data = blob_client.download_blob().readall()
        existing_csv = existing_blob_data.decode("utf-8")
        lines = existing_csv.strip().split("\n")

        # If the file is empty or missing a header, add one
        if len(lines) == 0 or not lines[0].startswith("time,question,answer,user_id"):
            lines = ["time,question,answer,user_id"] 
    except:
        # If not existing, create a new list with a header row
        lines = ["time,question,answer,user_id"] 

    # 2) Append the new record
    current_time = datetime.now().strftime("%H:%M:%S")

    # Safely handle commas or quotes by wrapping fields in quotes if needed
    # For simplicity, we just do a naive CSV approach here:
    row = [
        current_time,
        question.replace('"','""'),
        answer.replace('"','""'),
        "anonymous"  # or any user ID logic
    ]
    # Convert to a CSV line
    lines.append(",".join(f'"{item}"' for item in row))

    # 3) Re-upload the updated CSV back to blob
    new_csv_content = "\n".join(lines) + "\n"
    blob_client.upload_blob(new_csv_content, overwrite=True)

    # -------------------------------------------------------------------------
    return Answer
