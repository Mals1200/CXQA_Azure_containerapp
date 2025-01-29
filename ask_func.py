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
    LLM_DEPLOYMENT_NAME = "gpt-4o"
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
    )
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    # Hardcoded tables
    Tables = """
1. Al-Bujairy Footfalls.xlsx
   - Columns: {"Date": "datetime64[ns]", "Footfalls": "int64"}
2. Al-Turaif Footfalls.xlsx
   - Columns: {"Date": "datetime64[ns]", "Footfalls": "int64"}
3. Food and Beverage (F&B) Sales.xlsx
   - Columns: {"Restaurant name": "object", "Category": "object", "Date": "datetime64[ns]", "Covers": "float64", "Gross Sales": "float64"}
4. PE Observations.xlsx
   - Columns: {"Assessor Category": "object", "Assessor name": "object", "Date": "datetime64[ns]", "Week": "object", "checklist type": "object", "Area of assessment": "object", "Colleague name": "object", "Location": "object", "Number of Compliance": "int64", "Number of Non Compliance": "int64", "Total # of cases": "int64", "total compliance score": "float64", "Position": "object"}
5. Parking.xlsx
   - Columns: {"Date": "datetime64[ns]", "Valet Volume": "int64", "Valet Revenue": "int64", "Valet Utilization": "float64", "BCP Revenue": "object", "BCP Volume": "int64", "BCP Utilization": "float64", "SCP Volume": "int64", "SCP Revenue": "int64", "SCP Utilization": "float64"}
6. Qualitative Comments.xlsx
   - Columns: {"Open Ended": "object"}
7. Tickets.xlsx
   - Columns: {"Date": "datetime64[ns]", "Number of tickets": "int64", "revenue": "int64", "attendance": "int64", "Reservation Attendance": "int64", "Pass Attendance": "int64", "Male attendance": "int64", "Female attendance": "int64", "Rebate value": "float64", "AM Tickets": "int64", "PM Tickets": "int64", "Free tickets": "int64", "Paid tickets": "int64", "Free tickets %": "float64", "Paid tickets %": "float64", "AM Tickets %": "float64", "PM Tickets %": "float64"}
8. Top2Box Summary.xlsx
   - Columns: {"Month": "object", "Type": "object", "Top2Box": "float64"}
9. Total Landscape areas and quantities.xlsx
   - Columns: {"Assets": "object", "Unnamed: 1": "object", "Unnamed: 2": "object", "Unnamed: 3": "object"}
10. Violations.xlsx
    - Columns: {"Tenant": "object", "Department": "object", "Owner": "object", "Occurrence": "int64", "Status": "object", "Recent Date": "datetime64[ns]", "Issue": "object", "More Than 60 Days Period": "object"}
    """
    # Construct the prompt
    prompt = f"""
You are a decision-making assistant. You have access to a list of datafiles (with their columns) below.

**Rules**:
1. You may interpret partial or semantic matches between user text and the datafile names or columns. 
   - For example, if the user references a location or name that is close to the name of a datafile or its columns, treat it as referring to that datafile.
2. You can derive the day of the week from any "Date" column if needed.
3. Decide if the user’s question chat_history can be answered *theoretically* from these datafiles:
   - If you believe it *can* be answered (i.e., the datafiles contain the relevant columns or can derive them), answer **Python**.
   - Otherwise, answer **Index**.
   - Take into consideration the Chat_history with the question
4. Output strictly **"Python"** or **"Index"** with no other text.
5. If you were greeted return **Hello! How may I assist you?**
6. If you were asked somthing outside your scope say **This is outside of my scope, may I help you with anything else?**.

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
            "This is outside of my scope, may I help you with anything else?"
        ]
        return answer if answer in valid_responses else "Error"
    except Exception as e:
        return f"Error: {str(e)}"



# Global variable that will store the final content or answer
Content = None
def run_path(path: str, question: str = ""):
    """
    A single function that, based on the 'path' argument,
    runs either the "Index" code or the "Python" code.
    It stores the final result in a global variable called 'Content'.
    """
    global Content  # Declare that we want to modify the module-level 'Content'

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
        LLM_DEPLOYMENT_NAME = "gpt-4o"
        LLM_ENDPOINT = (
            "https://cxqaazureaihub2358016269.openai.azure.com/"
            "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
        )
        LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

        prompt = f"""
        You are a helpful assistant that answers the user question along the Chat_history by only using the provided information. 
        If the answer is not available reply with "No Information was Found".
        
        user:
        {question}
        
        Information
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
        except requests.exceptions.HTTPError as http_err:
            return "An error occurred while processing your request."
        except Exception:
            return "An unexpected error occurred."

    # -------------------------------------------------------------------------
    # INDEX PATH
    # -------------------------------------------------------------------------
    if path == "Index":
        # ==============================
        # Embeddings and Indexing Configuration (mlindex_content)
        # ==============================
        mlindex_content = """
embeddings:
  api_base: https://cxqaazureaihub2358016269.openai.azure.com
  api_type: azure
  api_version: 2023-07-01-preview
  batch_size: '16'
  connection:
    id: /subscriptions/f7102a7d-f032-4b41-b58c-4aae6daf6146/resourceGroups/cxqa_resource_group/providers/Microsoft.MachineLearningServices/workspaces/cxqa_genai_project/connections/cxqaazureaihub2358016269_aoai
    connection_type: workspace_connection
    deployment: text-embedding-ada-002
    dimension: 1536
    file_format_version: '2'
    kind: open_ai
    model: text-embedding-ada-002
    schema_version: '2'
index:
  api_version: 2024-05-01-preview
  connection:
    id: /subscriptions/f7102a7d-f032-4b41-b58c-4aae6daf6146/resourceGroups/cxqa_resource_group/providers/Microsoft.MachineLearningServices/workspaces/cxqa_genai_project/connections/cxqaazureaisearch
    connection_type: workspace_connection
    endpoint: https://cxqa-azureai-search.search.windows.net/
    engine: azure-sdk
  field_mapping:
    content: content
    embedding: contentVector
    filename: filepath
    metadata: meta_json_string
    title: title
    url: url
  index: cxqa-ind-v2
  kind: acs
  semantic_configuration_name: azureml-default
self:
  path:
    azureml://subscriptions/f7102a7d-f032-4b41-b58c-4aae6daf6146/resourcegroups/cxqa_resource_group/workspaces/cxqa_genai_project/datastores/workspaceblobstore/paths/azureml/63a0f8ea-2624-471d-a19b-27c568fdf096/index/
  asset_id:
    azureml://locations/eastus/workspaces/5d74a98c-1fc6-4567-8545-2632b489bd0b/data/cxqa-ind-v2/versions/1
"""

        # ==============================
        # Additional Parameters
        # ==============================
        query_type = "Hybrid (vector + keyword)"  # Could be "Vector" or "Keyword"
        top_k = 4

        # ==============================
        # Azure Cognitive Search Client Setup
        # ==============================
        SEARCH_SERVICE_NAME = "cxqa-azureai-search"
        SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
        INDEX_NAME = "cxqa-ind-v2"
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
            raise

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
        ind_meta = []
        for result in results:
            ind_data.append(result["content"])
            ind_meta.append(result["metadata"])

        Content = ind_data

        retrieved_info_str = "\n".join(str(item) for item in ind_data)
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
            LLM_DEPLOYMENT_NAME = "gpt-4o"
            LLM_ENDPOINT = (
                "https://cxqaazureaihub2358016269.openai.azure.com/"
                "openai/deployments/gpt-4o/chat/completions?api-version=2024-08-01-preview"
            )
            LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

            schema = """
Al-Bujairy Footfalls.xlsx: {'Date': dtype('<M8[ns]'), 'Footfalls': dtype('int64')}, 
Al-Turaif Footfalls.xlsx: {'Date': dtype('<M8[ns]'), 'Footfalls': dtype('int64')}, 
Food and Beverage (F&B) Sales.xlsx: {'Restaurant name': dtype('O'), 'Category': dtype('O'), 'Date': dtype('<M8[ns]'), 'Covers': dtype('float64'), 'Gross Sales': dtype('float64')}, 
PE Observations.xlsx: {'Assessor Category': dtype('O'), 'Assessor name ': dtype('O'), 'Date': dtype('<M8[ns]'), 'Week': dtype('O'), 'checklist type ': dtype('O'), 'Area of assessment ': dtype('O'), 'Colleague name': dtype('O'), 'Location ': dtype('O'), 'Number of Compliance': dtype('int64'), 'Number of Non Compliance': dtype('O'), 'Total # of cases': dtype('int64'), 'total compliance score': dtype('float64'), 'Position': dtype('O')}, 
Parking.xlsx: {'Date': dtype('<M8[ns]'), 'Valet Volume': dtype('int64'), 'Valet Revenue': dtype('int64'), 'Valet Utlization': dtype('float64'), 'BCP Revenue': dtype('O'), 'BCP Volume': dtype('int64'), 'BCP Utlization': dtype('float64'), 'SCP Volume': dtype('int64'), 'SCP Revenue': dtype('int64'), 'SCP Utlization': dtype('float64')}, 
Qualitative Comments.xlsx: {'Open Ended': dtype('O')}, 
Tickets.xlsx: {'Date': dtype('<M8[ns]'), 'Number of tickets': dtype('int64'), 'revenue': dtype('int64'), 'attendnace': dtype('int64'), 'Reservation Attendnace': dtype('int64'), 'Pass Attendance': dtype('int64'), 'Male attendance': dtype('int64'), 'Female attendance': dtype('int64'), 'Rebate value': dtype('float64'), 'AM Tickets': dtype('int64'), 'PM Tickets': dtype('int64'), 'Free tickets': dtype('int64'), 'Paid tickets': dtype('int64'), 'Free tickets %': dtype('float64'), 'Paid tickets %': dtype('float64'), 'AM Tickets %': dtype('float64'), 'PM Tickets %': dtype('float64'), 'Rebate Rate V 55': dtype('float64'), 'Revenue  v2': dtype('int64')}, 
Top2Box Summary.xlsx: {'Month': dtype('O'), 'Type': dtype('O'), 'Top2Box': dtype('float64')}, 
Total Landscape areas and quantities.xlsx: {'Assets': dtype('O'), 'Unnamed: 1': dtype('O'), 'Unnamed: 2': dtype('O'), 'Unnamed: 3': dtype('O')}, 
Violations.xlsx: {'Tenant\\u200b': dtype('O'), 'Department\\u200b': dtype('O'), 'Owner\\u200b': dtype('O'), 'Occurrence\\u200b': dtype('int64'), 'Status\\u200b': dtype('O'), '\\xa0Recent Date\\xa0\\u200b': dtype('<M8[ns]'), 'Issue': dtype('O'), 'More Than 60 Days Period': dtype('O')}
"""
            sample = """
Al-Bujairy Footfalls.xlsx: [{'Date': Timestamp('2023-01-01 00:00:00'), 'Footfalls': 2950}, {'Date': Timestamp('2023-01-02 00:00:00'), 'Footfalls': 2864}, {'Date': Timestamp('2023-01-03 00:00:00'), 'Footfalls': 4366}], 
Al-Turaif Footfalls.xlsx: [{'Date': Timestamp('2023-06-01 00:00:00'), 'Footfalls': 694}, {'Date': Timestamp('2023-06-02 00:00:00'), 'Footfalls': 1862}, {'Date': Timestamp('2023-06-03 00:00:00'), 'Footfalls': 1801}], 
Food and Beverage (F&B) Sales.xlsx: [{'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': Timestamp('2023-08-01 00:00:00'), 'Covers': 195.0, 'Gross Sales': 12536.65383}, {'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': Timestamp('2023-08-02 00:00:00'), 'Covers': 169.0, 'Gross Sales': 11309.05671}, {'Restaurant name': 'Angelina', 'Category': 'Casual Dining', 'Date': Timestamp('2023-08-03 00:00:00'), 'Covers': 243.0, 'Gross Sales': 17058.61479}]
PE Observations.xlsx: [{'Assessor Category': 'PE Team', 'Assessor name ': 'Moath Alyousef', 'Date': Timestamp('2023-08-01 00:00:00'), 'Week': '23-8-1', 'checklist type ': 'procedure', 'Area of assessment ': 'Wheelchair & Baby Stroller Rental', 'Colleague name': 'Yazeed Albatti', 'Location ': 'V3', 'Number of Compliance': 7, 'Number of Non Compliance': 1, 'Total # of cases': 8, 'total compliance score': 0.88, 'Position': 'Blank'}, {'Assessor Category': 'PE Team', 'Assessor name ': 'Moath Alyousef', 'Date': Timestamp('2023-08-01 00:00:00'), 'Week': '23-8-1', 'checklist type ': 'behaviour ', 'Area of assessment ': 'Wheelchair & Baby Stroller Rental', 'Colleague name': 'Yazeed Albatti', 'Location ': 'V3', 'Number of Compliance': 25, 'Number of Non Compliance': 0, 'Total # of cases': 25, 'total compliance score': 1.0, 'Position': 'Blank'}, {'Assessor Category': 'PE Team', 'Assessor name ': 'Moath Alyousef', 'Date': Timestamp('2023-08-01 00:00:00'), 'Week': '23-8-1', 'checklist type ': 'service promises', 'Area of assessment ': 'Wheelchair & Baby Stroller Rental', 'Colleague name': 'Yazeed Albatti', 'Location ': 'V3', 'Number of Compliance': 4, 'Number of Non Compliance': 0, 'Total # of cases': 4, 'total compliance score': 1.0, 'Position': 'Blank'}]
Parking.xlsx: [{'Date': Timestamp('2023-01-01 00:00:00'), 'Valet Volume': 194, 'Valet Revenue': 29100, 'Valet Utlization': 0.23, 'BCP Revenue': '               -  ', 'BCP Volume': 1951, 'BCP Utlization': 0.29, 'SCP Volume': 0, 'SCP Revenue': 0, 'SCP Utlization': 0.0}, {'Date': Timestamp('2023-01-02 00:00:00'), 'Valet Volume': 223, 'Valet Revenue': 33450, 'Valet Utlization': 0.27, 'BCP Revenue': '               -  ', 'BCP Volume': 1954, 'BCP Utlization': 0.29, 'SCP Volume': 0, 'SCP Revenue': 0, 'SCP Utlization': 0.0}, {'Date': Timestamp('2023-01-03 00:00:00'), 'Valet Volume': 243, 'Valet Revenue': 36450, 'Valet Utlization': 0.29, 'BCP Revenue': '               -  ', 'BCP Volume': 2330, 'BCP Utlization': 0.35, 'SCP Volume': 0, 'SCP Revenue': 0, 'SCP Utlization': 0.0}]
Qualitative Comments.xlsx: [{'Open Ended': 'يفوقو توقعاتي كل شيء رائع'}, {'Open Ended': 'وقليل اسعار التذاكر اجعل الجميع يستمتع بهذه التجربة الرائعة'}, {'Open Ended': 'إضافة كراسي هامة اكثر من المتوفر'}]
Tickets.xlsx: [{'Date': Timestamp('2023-01-01 00:00:00'), 'Number of tickets': 4644, 'revenue': 288050, 'attendnace': 2950, 'Reservation Attendnace': 0, 'Pass Attendance': 0, 'Male attendance': 1290, 'Female attendance': 1660, 'Rebate value': 131017.96, 'AM Tickets': 287, 'PM Tickets': 2663, 'Free tickets': 287, 'Paid tickets': 2663, 'Free tickets %': 0.09728813559322035, 'Paid tickets %': 0.9027118644067796, 'AM Tickets %': 0.09728813559322035, 'PM Tickets %': 0.9027118644067796, 'Rebate Rate V 55': 131017.96, 'Revenue  v2': 288050}, {'Date': Timestamp('2023-01-02 00:00:00'), 'Number of tickets': 7276, 'revenue': 205250, 'attendnace': 2864, 'Reservation Attendnace': 0, 'Pass Attendance': 0, 'Male attendance': 1195, 'Female attendance': 1669, 'Rebate value': 123698.68, 'AM Tickets': 978, 'PM Tickets': 1886, 'Free tickets': 978, 'Paid tickets': 1886, 'Free tickets %': 0.3414804469273743, 'Paid tickets %': 0.6585195530726257, 'AM Tickets %': 0.3414804469273743, 'PM Tickets %': 0.6585195530726257, 'Rebate Rate V 55': 123698.68, 'Revenue  v2': 205250}, {'Date': Timestamp('2023-01-03 00:00:00'), 'Number of tickets': 8354, 'revenue': 308050, 'attendnace': 4366, 'Reservation Attendnace': 0, 'Pass Attendance': 0, 'Male attendance': 1746, 'Female attendance': 2620, 'Rebate value': 206116.58, 'AM Tickets': 1385, 'PM Tickets': 2981, 'Free tickets': 1385, 'Paid tickets': 2981, 'Free tickets %': 0.3172240036646816, 'Paid tickets %': 0.6827759963353184, 'AM Tickets %': 0.3172240036646816, 'PM Tickets %': 0.6827759963353184, 'Rebate Rate V 55': 206116.58, 'Revenue  v2': 308050}], 
Top2Box Summary.xlsx: [{'Month': "Feb'23", 'Type': 'Bujairi Terrace/ Diriyah  offering', 'Top2Box': 0.9651741293532}, {'Month': "May'23", 'Type': 'Bujairi Terrace/ Diriyah  offering', 'Top2Box': 0.9186046511628}, {'Month': "June'23", 'Type': 'Bujairi Terrace/ Diriyah  offering', 'Top2Box': 0.7047308319739}], 
Total Landscape areas and quantities.xlsx: [{'Assets': 'SN', 'Unnamed: 1': 'Location', 'Unnamed: 2': 'Unit', 'Unnamed: 3': 'Quantity'}, {'Assets': 'Bujairi, Turaif Gardens, and Terraces', 'Unnamed: 1': nan, 'Unnamed: 2': nan, 'Unnamed: 3': nan}, {'Assets': '\xa0A', 'Unnamed: 1': 'Turaif Gardens', 'Unnamed: 2': nan, 'Unnamed: 3': nan}], Violations.xlsx: [{'Tenant\u200b': 'Brunch & Cake\xa0\u200b', 'Department\u200b': 'Asset Management\u200b', 'Owner\u200b': 'Andrew Nasr\u200b', 'Occurrence\u200b': 9, 'Status\u200b': 'Open\u200b', '\xa0Recent Date\xa0\u200b': Timestamp('2023-08-10 00:00:00'), 'Issue': 'Health Certificate', 'More Than 60 Days Period': 'Yes'}, {'Tenant\u200b': 'Sinatra\u200b', 'Department\u200b': 'Asset Management\u200b', 'Owner\u200b': 'Andrew Nasr\u200b', 'Occurrence\u200b': 9, 'Status\u200b': 'Open\u200b', '\xa0Recent Date\xa0\u200b': Timestamp('2024-01-31 00:00:00'), 'Issue': 'Health Certificate', 'More Than 60 Days Period': 'Yes'}, {'Tenant\u200b': 'Tatel\u200b', 'Department\u200b': 'Asset Management\u200b', 'Owner\u200b': 'Andrew Nasr\u200b', 'Occurrence\u200b': 9, 'Status\u200b': 'Closed\u200b', '\xa0Recent Date\xa0\u200b': Timestamp('2024-06-09 00:00:00'), 'Issue': 'Expired Food', 'More Than 60 Days Period': 'No'}], 
"""

            system_prompt = f"""
You are a python expert. Use the user Question along the Chat_history and the Chat_history to make the python code that will get the answer from dataframes schemas and samples. 
Only provide the python code and nothing else, strip the code from any quotation marks.
Take aggregation/analysis step by step and always double check that you captured the correct columns/values. 
Don't give examples, only provide the actual code. If you can't provide the code, say "404" and make sure it's a string.

User question:
{user_question}

Dataframes schemas:
{schema}

Dataframes samples:
{sample}

Chat_history:
{chat_history}

Example code you should write for questions like "What is the total footfall in Al Turaif on 1st October 2023?":

from datetime import datetime

# Find the file with the relevant data
filename = 'At-Turaif Footfalls.xlsx'

# Load the data into a pandas dataframe
df = pd.read_excel(filename)

# Convert the Date column to a datetime object
df['Date'] = pd.to_datetime(df['Date'])

# Filter the dataframe to only include the relevant date
date_filter = df['Date'] == datetime(2023, 10, 1)
df_filtered = df[date_filter]

# Get the footfall for the relevant date
footfall = df_filtered['Footfalls'].iloc[0]

print("The footfall in Al Turaif on 1st of October 2023 is:", footfall)
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
        if The_Code == "404" or The_Code.startswith("Error"):
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
    number_of_messages = 10  # Total messages  of both user and assisstant
    max_pairs = number_of_messages // 2  # pairs to retain
    max_entries = max_pairs * 2
    
    # 3) Generate answer
    path_decision = Path_LLM(question)
    answer = run_path(path_decision, question)
    
    # 4) Append assistant's answer
    chat_history.append(f"Assistant: {answer}")
    
    # 5) FINAL truncation (after both messages are added)
    chat_history = chat_history[-max_entries:]  # Now ensures pairs stay together

    Answer = f"User: {question}\nAssisstant: {answer}"
    return Answer


# ==============================
# Use
# ==============================
# question1 = "Hello"
# print(Ask_Question(question1))
# print("=======================\n\n\n")
