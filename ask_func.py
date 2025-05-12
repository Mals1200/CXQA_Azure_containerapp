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
from tenacity import retry, stop_after_attempt, wait_fixed  # retrying
from functools import lru_cache, wraps
from collections import OrderedDict
import difflib
import time

#######################################################################################
#                               GLOBAL CONFIG / CONSTANTS
#######################################################################################
#######################################################################################
#                               GLOBAL CONFIG / CONSTANTS
#######################################################################################
CONFIG = {
    # ── MAIN, high-capacity model (Tool-1 Index, Tool-2 Python, Tool-3 Fallback) ──
    "LLM_ENDPOINT"     : "https://malsa-m3q7mu95-eastus2.cognitiveservices.azure.com/"
                         "openai/deployments/gpt-4.1/chat/completions?api-version=2025-01-01-preview",

    # same key used for both deployments
    "LLM_API_KEY"      : "5EgVev7KCYaO758NWn5yL7f2iyrS4U3FaSI5lQhTx7RlePQ7QMESJQQJ99AKACHYHv6XJ3w3AAAAACOGoSfb",

    # ── AUXILIARY model (classifiers, splitters, etc.) ────────────────────────────
    "LLM_ENDPOINT_AUX" : "https://malsa-m3q7mu95-eastus2.cognitiveservices.azure.com/"
                         "openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview",

    # (unchanged settings below) ───────────────────────────────────────────────────
    "SEARCH_SERVICE_NAME": "cxqa-azureai-search",
    "SEARCH_ENDPOINT"    : "https://cxqa-azureai-search.search.windows.net",
    "ADMIN_API_KEY"      : "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv",
    "INDEX_NAME"         : "vector-1746718296853-08-05-2025",  #"vector-1741865904949",
    "SEMANTIC_CONFIG_NAME": "vector-1746718296853-08-05-2025-semantic-configuration", #"vector-1741865904949-semantic-configuration",
    "CONTENT_FIELD"      : "chunk",
    "ACCOUNT_URL"        : "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "SAS_TOKEN"          : (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    ),
    "CONTAINER_NAME"    : "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore",
    "TARGET_FOLDER_PATH": "UI/2024-11-20_142337_UTC/cxqa_data/tabular/"
}


# Global objects with better initialization
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

# Initialize with empty values that will be set per conversation
chat_history = []
recent_history = []
tool_cache = {}

# Add retry decorator for Azure API calls
def azure_retry(max_attempts=3, delay=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        time.sleep(delay * (attempt + 1))  # Exponential backoff
                    logging.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            raise last_exception
        return wrapper
    return decorator

#######################################################################################
#                           RBAC HELPERS (User & File Tiers)
#######################################################################################
@azure_retry()
def load_rbac_files():
    """
    Loads User_rbac.xlsx and File_rbac.xlsx from the RBAC folder in Azure Blob Storage, 
    returns them as two DataFrame objects: (df_user, df_file).
    If anything fails, returns two empty dataframes.
    """
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]

    rbac_folder_path = "UI/2024-11-20_142337_UTC/cxqa_data/RBAC/"
    user_rbac_file = "User_rbac.xlsx"
    file_rbac_file = "File_rbac.xlsx"

    df_user = pd.DataFrame()
    df_file = pd.DataFrame()

    try:
        blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
        container_client = blob_service_client.get_container_client(container_name)

        # Load User_rbac.xlsx
        user_rbac_blob = container_client.get_blob_client(rbac_folder_path + user_rbac_file)
        user_rbac_data = user_rbac_blob.download_blob().readall()
        df_user = pd.read_excel(BytesIO(user_rbac_data))

        # Load File_rbac.xlsx
        file_rbac_blob = container_client.get_blob_client(rbac_folder_path + file_rbac_file)
        file_rbac_data = file_rbac_blob.download_blob().readall()
        df_file = pd.read_excel(BytesIO(file_rbac_data))

    except Exception as e:
        logging.error(f"Failed to load RBAC files: {e}")
    
    return df_user, df_file

def get_file_tier(file_name):
    """
    Checks the file name in the File_rbac.xlsx file, returns the tier needed to access it.
    Now uses fuzzy matching via difflib to find the best match if the exact or partial
    match isn't found. If best match ratio is below 0.8, defaults to tier=1.
    """
    _, df_file = load_rbac_files()
    if df_file.empty or ("File_Name" not in df_file.columns) or ("Tier" not in df_file.columns):
        # default if not loaded or columns missing
        return 1  
    
    # Remove common extensions and make it all lower-case
    base_file_name = (
        file_name.lower()
        .replace(".pdf", "")
        .replace(".xlsx", "")
        .replace(".xls", "")
        .replace(".csv", "")
        .strip()
    )
    
    # If the user-provided name is empty after cleaning, just default
    if not base_file_name:
        return 1

    # We'll track the best fuzzy ratio and best tier found so far
    best_ratio = 0.0
    best_tier = 1

    for idx, row in df_file.iterrows():
        # Also remove common extensions and lower
        row_file_raw = str(row["File_Name"])
        row_file_clean = (
            row_file_raw.lower()
            .replace(".pdf", "")
            .replace(".xlsx", "")
            .replace(".xls", "")
            .replace(".csv", "")
            .strip()
        )
        
        # Compare the two strings with difflib
        ratio = difflib.SequenceMatcher(None, base_file_name, row_file_clean).ratio()
        
        # If we get a better ratio, store that match
        if ratio > best_ratio:
            best_ratio = ratio
            try:
                best_tier = int(row["Tier"])
            except:
                best_tier = 1
    
    # If our best match ratio is below some threshold (e.g. 0.8), we treat it as "no match"
    if best_ratio < 0.8:
        # Could print a debug if desired:
        # print(f"[DEBUG get_file_tier] best_ratio={best_ratio:.2f} => default tier=1")
        return 1
    else:
        # Found a good fuzzy match
        # print(f"[DEBUG get_file_tier] Fuzzy matched => ratio={best_ratio:.2f}, tier={best_tier}")
        return best_tier


#######################################################################################
#                           TABLES / SCHEMA / SAMPLE GENERATION (DYNAMIC)
#######################################################################################
@lru_cache(maxsize=1)
def load_table_metadata(sample_n: int = 2):
    container = BlobServiceClient(account_url=CONFIG["ACCOUNT_URL"], credential=CONFIG["SAS_TOKEN"])\
                    .get_container_client(CONFIG["CONTAINER_NAME"])
    prefix = CONFIG["TARGET_FOLDER_PATH"]
    meta = OrderedDict()

    for blob in container.list_blobs(name_starts_with=prefix):
        fn = os.path.basename(blob.name)
        if not fn.lower().endswith((".xlsx", ".xls", ".csv")):
            continue

        data = container.get_blob_client(blob.name).download_blob().readall()
        df = (pd.read_excel if fn.lower().endswith((".xlsx", ".xls")) else pd.read_csv)(BytesIO(data))

        schema = {col: str(dt) for col, dt in df.dtypes.items()}
        sample = df.head(sample_n).to_dict(orient="records")
        meta[fn] = {"schema": schema, "sample": sample}

    return meta

def format_tables_text(meta: dict) -> str:
    lines = []
    for i, (fn, info) in enumerate(meta.items(), 1):
        lines.append(f'{i}) "{fn}", with the following tables:')
        for col, dt in info["schema"].items():
            lines.append(f"   -{col}: {dt}")
    return "\n".join(lines)

def format_schema_and_sample(meta: dict, sample_n: int = 2, char_limit: int = 15) -> str:
    def truncate_val(v):
        s = "" if v is None else str(v)
        return s if len(s) <= char_limit else s[:char_limit] + "…"

    lines = []
    for fn, info in meta.items():
        lines.append(f"{fn}: {info['schema']}")
        truncated = [
            {col: truncate_val(val) for col, val in row.items()}
            for row in info["sample"][:sample_n]
        ]
        lines.append(f"    Sample: {truncated},")
    return "\n".join(lines)

_metadata   = load_table_metadata(sample_n=2)
TABLES      = format_tables_text(_metadata)
SCHEMA_TEXT = format_schema_and_sample(_metadata, sample_n=2, char_limit=15)
#SAMPLE_TEXT = SCHEMA_TEXT  # if SAMPLE_TEXT needed separately

#######################################################################################
#                   CENTRALIZED LLM CALL (Point #1 Optimization)
#######################################################################################
def call_llm(system_prompt, user_prompt, max_tokens=500, temperature=0.0):
    """
    Central helper for calling Azure OpenAI LLM.
    Handles requests.post, checks for errors, and returns the content string.
    Improved to ensure we do not return an empty string silently.
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "api-key": CONFIG["LLM_API_KEY"]
        }
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
            content = data["choices"][0]["message"].get("content", "").strip()
            if content:
                return content
            else:
                logging.warning("LLM returned an empty content field.")
                return "No content from LLM."
        else:
            logging.warning(f"LLM returned no choices: {data}")
            return "No choices from LLM."
    except Exception as e:
        # make the real cause obvious (rate‑limit, token overflow, etc.)
        err_msg = f"LLM Error: {e}"
        if hasattr(e, "response") and e.response is not None:           # Azure/OpenAI gives details here
            err_msg += f" | Azure response: {e.response.text}"
        print(err_msg)                                                  # <‑‑ NEW: show in console/stdout
        logging.error(err_msg)
        return err_msg

#######################################################################################
#                                 auxiliary caller
#######################################################################################
def call_llm_aux(system_prompt, user_prompt, max_tokens=300, temperature=0.0):
    """
    Lightweight LLM caller that targets the GPT-4o auxiliary deployment.
    Used for classifiers, question splitters, etc. — NOT for Tool-1/2/3.
    """
    import requests, time, logging, json

    headers = {
        "Content-Type": "application/json",
        "api-key": CONFIG["LLM_API_KEY"]
    }
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature
    }

    for attempt in range(3):
        try:
            r = requests.post(CONFIG["LLM_ENDPOINT_AUX"], headers=headers, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            return (
                data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                or "No content from LLM."
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as e:
            logging.error(f"AUX LLM error: {e}")
            return f"LLM Error: {e}"
    return "LLM Error: exceeded aux model rate limit"


#######################################################################################
#                   COMBINED TEXT CLEANING (Point #2 Optimization)
#######################################################################################
def clean_text(text: str) -> str:
    """
    Combine repeated cleaning logic into a single function.
    Removes repeated words, repeated patterns, excessive punctuation/spaces, etc.
    """
    if not text:
        return text

    # 1) Remove repeated words like: "TheThe", "total total"
    text = re.sub(r'\b(\w+)( \1\b)+', r'\1', text, flags=re.IGNORECASE)

    # 2) Remove repeated characters within a word: e.g., "footfallsfalls"
    text = re.sub(r'\b(\w{3,})\1\b', r'\1', text, flags=re.IGNORECASE)

    # 3) Remove excessive punctuation or spaces
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.{3,}', '...', text)

    return text.strip()

#######################################################################################
#              KEEPING deduplicate_streaming_tokens & is_repeated_phrase
#######################################################################################
def deduplicate_streaming_tokens(last_tokens, new_token):
    if last_tokens.endswith(new_token):
        return ""
    return new_token

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

#######################################################################################
#                              SUBQUESTION SPLITTING
#######################################################################################
def split_question_into_subquestions(user_question, use_semantic_parsing=True):
    """
    Splits a user question into subquestions using either a regex-based approach
    or a semantic parsing approach.
    """
    if not user_question.strip():
        return []

    if not use_semantic_parsing:
        # Regex-based splitting (e.g., "and" or "&")
        text = re.sub(r"\s+and\s+", " ~SPLIT~ ", user_question, flags=re.IGNORECASE)
        text = re.sub(r"\s*&\s*", " ~SPLIT~ ", text)
        parts = text.split("~SPLIT~")
        subqs = [p.strip() for p in parts if p.strip()]
        return subqs
    else:
        system_prompt = (
            "You are a helpful assistant. "
            "You receive a user question which may have multiple parts. "
            "Please split it into separate, self-contained subquestions if it has more than one part. "
            "If it's only a single question, simply return that one. "
            "Return each subquestion on a separate line or as bullet points."
        )

        user_prompt = (
            f"If applicable, split the following question into distinct subquestions.\n\n"
            f"{user_question}\n\n"
            f"If not applicable, just return it as is."
        )

        answer_text = call_llm_aux(system_prompt, user_prompt, max_tokens=300, temperature=0.0)
        lines = [
            line.lstrip("•-0123456789). ").strip()
            for line in answer_text.split("\n")
            if line.strip()
        ]
        subqs = [l for l in lines if l]

        if not subqs:
            subqs = [user_question]
        return subqs

#######################################################################################
#                 REFERENCES CHECK & RELEVANCE CHECK  (Points #3 + #1 synergy)
#######################################################################################
def references_tabular_data(question, tables_text):
    llm_system_message = (
        "You are a strict YES/NO classifier. Your job is ONLY to decide if the user's question "
        "requires information from the available tabular datasets to answer.\n"
        "You must respond with EXACTLY one word: 'YES' or 'NO'.\n"
        "Do NOT add explanations or uncertainty. Be strict and consistent."
    )
    llm_user_message = f"""
    User Question:
    {question}
    
    Available Tables:
    {tables_text}
    
    Decision Rules:
    1. Reply 'YES' ONLY if the question explicitly asks for numerical facts, figures, statistics, totals, direct calculations from table columns, or specific record lookups that are clearly obtainable from the structured datasets listed in Available Tables.
    2. Reply 'NO' if the question is general, opinion-based, theoretical, policy-related, or does not require specific numerical data directly from these tables.
    3. Completely ignore the sample rows of the tables. Assume full datasets exist beyond the samples.
    4. Be STRICT: only reply 'NO' if you are CERTAIN the tables are not needed for direct data extraction.
    5. Do NOT create or assume data. Only decide if the listed tabular data is NEEDED to answer the User Question by directly querying the table.
    6. Base your decision ONLY on the User Question and the list of Available Tables. IGNORE any potential chat history.
    7. Questions asking for qualitative summaries, opinions, 'areas of improvement', 'key findings', or general topics often found in narrative reports or policy documents should be classified as 'NO', even if they mention dates or entities that might also appear in tables, UNLESS the question specifically asks for quantifiable metrics, counts, or statistics directly from those tables.

    Final instruction: Reply ONLY with 'YES' or 'NO'.
    """
    llm_response = call_llm_aux(llm_system_message, llm_user_message, max_tokens=5, temperature=0.0)
    clean_response = llm_response.strip().upper()
    return "YES" in clean_response

# In ask_func_client_2.py
# Replace your existing is_text_relevant function with this:
def is_text_relevant(question, snippet, question_needs_tables_too: bool): # Added new parameter
    if not snippet or not snippet.strip():
        logging.debug("[Relevance Check] Snippet is empty, returning False.")
        return False

    context_guidance = ""
    if question_needs_tables_too:
        context_guidance = (
            "The User Question is also expected to be answered by data from tables. "
            "Therefore, this Text Snippet is relevant ONLY IF it provides crucial context, "
            "definitions, or directly related information that the tables might not offer for this specific question. "
            "General mentions of the same topics, entities, or dates found in broad reports are LESS LIKELY to be relevant "
            "if the core answer is expected from a table."
        )
    else: # Question does NOT need tables, so index is primary source for it
        context_guidance = (
            "The User Question is expected to be answered primarily by text documents like this Snippet. "
            "Therefore, consider it relevant if it addresses the question's topic, keywords, or provides background."
        )

    system_prompt = (
        "You are an expert relevance classifier. Your goal is to determine if the provided text Snippet "
        "contains information that could DIRECTLY help answer the User Question or is highly related.\n"
        f"{context_guidance}\n"
        "Focus on keywords, topics, and entities. "
        "Consider the snippet relevant even if it only partially answers the question or provides essential background context, "
        "especially if it's from a policy or procedure document for a how-to question.\n"
        "Be critical for general report snippets if the question is very specific and likely answerable by data tables.\n"
        "Respond ONLY with 'YES' or 'NO'."
    )
    max_snippet_len = 500 # Truncate long snippets for the prompt
    snippet_for_prompt = snippet[:max_snippet_len] + "..." if len(snippet) > max_snippet_len else snippet
    
    user_prompt = f"User Question:\n{question}\n\nText Snippet:\n{snippet_for_prompt}\n\nIs this snippet relevant? Respond YES or NO."
    
    content = call_llm_aux(system_prompt, user_prompt, max_tokens=10, temperature=0.0)
    # Keep this one debug line to see the direct output of the relevance check
    #print(f"DEBUG: [Relevance Check] Q: '{question[:50]}...' NeedsTables: {question_needs_tables_too} -> LLM Raw Response: '{content}'")
    is_relevant_flag = content.strip().upper().startswith("YES")
    return is_relevant_flag
    

#######################################################################################
#                              TOOL #1 - Index Search
#######################################################################################
# --- Modified tool_1_index_search with detailed logging ---
@azure_retry()
def tool_1_index_search(user_question, top_k=5, user_tier=1, question_primarily_tabular: bool = False):
    """
    Modified version: uses split_question_into_subquestions to handle multi-part queries.
    Then filters out docs the user has no access to, before final top_k selection.
    Includes detailed DEBUG logging.
    """
    SEARCH_SERVICE_NAME = CONFIG["SEARCH_SERVICE_NAME"]
    SEARCH_ENDPOINT = CONFIG["SEARCH_ENDPOINT"]
    ADMIN_API_KEY = CONFIG["ADMIN_API_KEY"]
    INDEX_NAME = CONFIG["INDEX_NAME"]
    SEMANTIC_CONFIG_NAME = CONFIG["SEMANTIC_CONFIG_NAME"]
    CONTENT_FIELD = CONFIG["CONTENT_FIELD"]

    # --- Added Log ---
    #print(f"DEBUG: [Tool 1] Entering for question '{user_question[:50]}...'")

    subquestions = split_question_into_subquestions(user_question, use_semantic_parsing=True)
    if not subquestions:
        subquestions = [user_question]
    # --- Added Log ---
    #print(f"DEBUG: [Tool 1] Subquestions: {subquestions}")

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        merged_docs = []
        all_raw_results_count = 0 # To count total raw results
        for subq in subquestions:
            # --- Added Log ---
            #print(f"DEBUG: [Tool 1] Searching index for subquestion: '{subq}'")
            results = search_client.search(
                search_text=subq,
                query_type="semantic",
                semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                top=top_k,
                select=["title", CONTENT_FIELD],
                include_total_count=True # Get total count if possible (check API support)
            )

            # --- Log raw results found BEFORE filtering ---
            raw_results_list = list(results) # Convert iterator to list to inspect
            current_batch_count = len(raw_results_list)
            all_raw_results_count += current_batch_count
            #print(f"DEBUG: [Tool 1] Raw search returned {current_batch_count} results for '{subq}':")
            for i, r in enumerate(raw_results_list):
                 snippet = r.get(CONTENT_FIELD, "").strip()
                 title = r.get("title", "").strip()
                 #print(f"DEBUG: [Tool 1]   Raw {i+1}: Title='{title}', Snippet='{snippet[:60]}...'")
                 if snippet:
                     # Add to merged_docs only if snippet exists
                     merged_docs.append({"title": title, "snippet": snippet})
            # --- End log raw results ---

        # --- Added Log ---
        #print(f"DEBUG: [Tool 1] Total raw results found across subquestions: {all_raw_results_count}")
        if not merged_docs:
            # --- Added Log ---
            #print("DEBUG: [Tool 1] No documents found with non-empty snippets after initial search.")
            return {"top_k": "No information", "file_names": []}

        # Filter by access + relevance
        relevant_docs = []
        # --- Added Log ---
        #print(f"DEBUG: [Tool 1] Filtering {len(merged_docs)} merged docs by RBAC + Relevance...")
        for i, doc in enumerate(merged_docs):
            snippet = doc["snippet"]
            title = doc["title"]
             # --- Added Log ---
            #print(f"DEBUG: [Tool 1]  Filtering doc {i+1}/{len(merged_docs)}: Title='{title}'")
            file_tier = get_file_tier(title)
            rbac_pass = user_tier >= file_tier
            # --- Added Log ---
            #print(f"DEBUG: [Tool 1]   RBAC Check: UserTier={user_tier}, FileTier={file_tier}, Pass={rbac_pass}")
            if rbac_pass:
                # --- Log relevance check ---
                #print(f"DEBUG: [Tool 1]   Checking relevance for snippet: '{snippet[:60]}...'")
                is_relevant_result = is_text_relevant(user_question, snippet, question_primarily_tabular) # Call relevance check
                # --- Added Log ---
                #print(f"DEBUG: [Tool 1]   Relevance Check Result: {is_relevant}")
                # --- End log relevance check ---
                if is_relevant_result: # Actually use the result for filtering
                    relevant_docs.append(doc)
                #print(f"DEBUG: [Tool 1]   >>> Doc {i+1} passed RBAC, ADDED to relevant_docs (Relevance ignored).")
            #else:
                 # --- Added Log ---
                 #print(f"DEBUG: [Tool 1]   --- Doc {i+1} failed RBAC check.")

        if not relevant_docs:
             # --- Added Log ---
            #print("DEBUG: [Tool 1] No documents remaining after RBAC/Relevance filtering.")
            return {"top_k": "No information", "file_names": []}

        # Weighted scoring (Keep as is)
        for doc in relevant_docs:
            ttl = doc["title"].lower()
            score = 0
            if "policy" in ttl: score += 10
            if "report" in ttl: score += 5
            if "sop" in ttl: score += 3
            doc["weight_score"] = score

        docs_sorted = sorted(relevant_docs, key=lambda x: x["weight_score"], reverse=True)
        docs_top_k = docs_sorted[:top_k]

        # Extract file names and texts separately - ensure no duplicates
        # Corrected this logic slightly from previous thought
        file_names_final = []
        seen_titles = set()
        for d in docs_top_k:
            title = d["title"]
            if title not in seen_titles:
                file_names_final.append(title)
                seen_titles.add(title)
        file_names_final = file_names_final[:3] # Apply limit after ensuring uniqueness

        re_ranked_texts = [d["snippet"] for d in docs_top_k]
        combined = "\n\n---\n\n".join(re_ranked_texts)

        # --- Log final return ---
        final_dict = {"top_k": combined, "file_names": file_names_final}
        #print(f"DEBUG: [Tool 1] Returning: file_names={final_dict['file_names']}, top_k snippet count={len(docs_top_k)}")
        return final_dict
        # --- End log final return ---

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        # --- Added Log ---
        #print(f"DEBUG: [Tool 1] Error encountered: {e}")
        return {"top_k": "No information", "file_names": []}

# --- End of modified tool_1_index_search ---

#######################################################################################
#                 HELPER to check table references vs. user tier
#######################################################################################
def reference_table_data(code_str, user_tier):
    """
    Scans the generated Python code for references to specific table filenames 
    (like "Al-Bujairy Terrace Footfalls.xlsx" etc.). For each referenced file, we check 
    the file tier from File_rbac.xlsx. If the user tier < file tier => no access => 
    we immediately return a short message that the user is not authorized.

    If all references are okay, return None (meaning "all good").
    """
    # We'll look for patterns like "dataframes.get("SomeFile.xlsx") or the actual file references 
    pattern = re.compile(
    r'(?:dataframes\.get|pd\.read_(?:excel|csv))\(\s*[\'"]([^\'"]+)[\'"]\s*\)'
    )
    found_files = pattern.findall(code_str)
    unique_files = list(set(found_files))

    # Modify the loop to iterate over unique_files instead of found_files:
    for fname in unique_files:
        # ... rest of the loop checking required_tier ...
        required_tier = get_file_tier(fname)
        if user_tier < required_tier:
            return f"User does not have access to {fname} (requires tier {required_tier})."

    return None # all good

#######################################################################################
#                              TOOL #2 - Code Run
#######################################################################################
@azure_retry()
@azure_retry()
def tool_2_code_run(user_question, user_tier=1, recent_history=None):
    #if not references_tabular_data(user_question, TABLES):
        #return {"result": "No information", "code": "", "table_names": []}

    # Centralize fallback logic for chat history
    rhistory = recent_history if recent_history else []

    system_prompt = f"""
You are a python expert. Use the User Question along with the Chat_history to make the python code that will get the answer from the provided Dataframes schemas and samples.
Only provide the python code and nothing else, without any markdown fences like ```python or ```.
Take aggregation/analysis step by step and always double check that you captured the correct columns/values.
Don't give examples, only provide the actual code. If you can't provide the code, say "404" as a string.

**General Rules**:
1. Only use columns that actually exist as per the schemas. Do NOT invent columns or table names.
2. Don't rely on sample rows for data content; the real dataset can have more/different data. Always reference columns as shown in the schemas.
3. Return pure Python code that can run as-is, including necessary imports (like `import pandas as pd`).
4. The code must produce a final `print()` statement with the answer. If multiple pieces of information are requested, print them clearly labeled.
5. If a user references a column/table that does not exist in the schemas, return "404".
6. Use semantic reasoning to handle synonyms or minor typos for table/column names if they reasonably map to the provided schemas.
7. Do not use Chat_history information directly within the generated code logic or print statements, but use it for context if needed to understand the user's question.

**Data Handling Rules for Pandas Code**:
A. **Numeric Conversion:** When a column is expected to be numeric for calculations (e.g., for .sum(), .mean(), comparisons):
   - First, replace common non-numeric placeholders (like '-', 'N/A', or strings containing only spaces) with `pd.NA` or `numpy.nan`. For example: `df['column_name'] = df['column_name'].replace(['-', 'N/A', ' ', '  '], pd.NA)`
   - Then, explicitly convert the column to a numeric type using `pd.to_numeric(df['column_name'], errors='coerce')`. This will turn any remaining unparseable values into `NaN`.
B. **Handle NaN Values:** Before performing aggregate functions (like `.sum()`, `.mean()`) or arithmetic operations on numeric columns, ensure `NaN` values are handled, e.g., by using `skipna=True` (which is default for many aggregations like `.sum()`) or by explicitly filling them (e.g., `df['numeric_column'].fillna(0).sum()`).
C. **Date Columns:** If the question involves dates:
   - Convert date-like columns to datetime objects using `pd.to_datetime(df['Date_column'], errors='coerce')`.
   - When comparing or merging data based on dates across multiple dataframes, ensure date columns are of a consistent datetime type and format. Be careful with operations that require aligned date indexes.
D. **Complex Lookups:** For questions requiring data from multiple tables (e.g., "find X in table A on the date of max Y in table B"):
   - First, determine the intermediate value (e.g., the date of max Y).
   - Then, use that value to filter/query the second table.
   - Ensure data types are compatible for lookups or merges.
E. **Error Avoidance:** Generate code that is robust. If a filtering step might result in an empty DataFrame or Series, check for this (e.g., `if not df_filtered.empty:`) before trying to access elements by index (e.g., `.iloc[0]`) or perform calculations that would fail on empty data. If data is not found after filtering, print a message like "No data available for the specified criteria." 

User question:
{user_question}

Dataframes schemas and sample:
{SCHEMA_TEXT}

Chat_history:
{rhistory}
"""

    code_str = call_llm_aux(system_prompt, user_question, max_tokens=1200, temperature=0.7)

    if not code_str or code_str == "404":
        return {"result": "No information", "code": "", "table_names": []}

    # Check references vs. user tier
    access_issue = reference_table_data(code_str, user_tier)
    if access_issue:
        # Return a short "no access" style message
        return {"result": access_issue, "code": "", "table_names": []}
    
    # Extract table names from the code - check both patterns
    table_names = []
    
    # Pattern 1: dataframes.get("filename")
    pattern1 = re.compile(r'dataframes\.get\(\s*[\'"]([^\'"]+)[\'"]\s*\)')
    matches1 = pattern1.findall(code_str)
    if matches1:
        for match in matches1:
            if match not in table_names:
                table_names.append(match)
    
    # Pattern 2: pd.read_excel("filename") or pd.read_csv("filename")
    pattern2 = re.compile(r'pd\.read_(?:excel|csv)\(\s*[\'"]([^\'"]+)[\'"]\s*\)')
    matches2 = pattern2.findall(code_str)
    if matches2:
        for match in matches2:
            if match not in table_names:
                table_names.append(match)
    
    # Limit to max 3 table names, but keep file extensions
    table_names = table_names[:3]

    #print(f"DEBUG: For question '{user_question[:50]}...'") # Identify which question run
    #print(f"DEBUG: Generated code_str:\n---\n{code_str}\n---")
    #print(f"DEBUG: Extracted table_names: {table_names}")
    #This line was changed to include only the tables needed
    execution_result = execute_generated_code(code_str, required_tables=table_names) # Pass table_names
    return {"result": execution_result, "code": code_str, "table_names": table_names}

def execute_generated_code(code_str, required_tables=None):
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]
    target_folder_path = CONFIG["TARGET_FOLDER_PATH"]

    dataframes = {} # Initialize dict to store loaded dataframes
    # --- MODIFICATION START ---
    # Only proceed to load data if specific tables are requested
    if required_tables:
        #logging.info(f"Attempting to load required tables: {required_tables}")
        try:
            blob_service_client = BlobServiceClient(account_url=account_url, credential=sas_token)
            container_client = blob_service_client.get_container_client(container_name)

            for file_name in required_tables:
                # Construct the full path to the blob
                blob_name = os.path.join(target_folder_path, file_name).replace("\\", "/") # Ensure forward slashes for blob path

                try:
                    #logging.debug(f"Loading blob: {blob_name}")
                    blob_client = container_client.get_blob_client(blob_name)
                    blob_data = blob_client.download_blob().readall()

                    # Read into DataFrame based on file extension
                    if file_name.lower().endswith(('.xlsx', '.xls')):
                        df = pd.read_excel(io.BytesIO(blob_data))
                        dataframes[file_name] = df
                        #logging.debug(f"Successfully loaded Excel file: {file_name}")
                    elif file_name.lower().endswith('.csv'):
                        df = pd.read_csv(io.BytesIO(blob_data))
                        dataframes[file_name] = df
                        #logging.debug(f"Successfully loaded CSV file: {file_name}")
                    #else:
                        #logging.warning(f"Skipping file with unsupported extension in required list: {file_name}")

                except Exception as blob_error:
                    # Handle error if a specific required blob is not found or fails to load
                    err_msg = f"Error loading required table '{blob_name}': {blob_error}"
                    print(err_msg)
                    logging.error(err_msg)
                    # Stop execution and return error if a required file is missing/unreadable
                    return err_msg

        except Exception as service_error:
            # Handle Azure connection errors
            err_msg = f"Azure connection error during selective table loading: {service_error}"
            print(err_msg)
            logging.error(err_msg)
            return err_msg
    else:
        # If required_tables is empty or None, but code seems to need dataframes
        # Check if the code likely expects dataframes
        # This check is basic; might need refinement based on code generation patterns
        if "dataframes.get(" in code_str or "pd.read_excel(" in code_str or "pd.read_csv(" in code_str:
            logging.warning("Code execution might expect tables, but none were identified as required.")
            # You could return an error, or let it proceed and potentially fail during exec
            # Returning an error is likely safer.
            return "Error: Code seems to require tables, but specific tables needed were not identified or provided."
        else:
            # If code doesn't appear to reference dataframes, proceed without loading any.
            logging.info("No required tables specified, proceeding without loading data.")

    # --- MODIFICATION END ---


    # --- Safety check before exec ---
    # If the code expects dataframes but none were loaded (e.g., due to errors above)
    # This check might overlap with the one above, but adds safety before exec
    if not dataframes and ("dataframes.get(" in code_str):
         return "Error: Failed to load required tables before code execution."


    # Modify code string (replace pandas read calls with dataframe dictionary lookups)
    # This part remains the same
    code_modified = code_str.replace("pd.read_excel(", "dataframes.get(")
    code_modified = code_modified.replace("pd.read_csv(", "dataframes.get(")

    # Execute the code
    output_buffer = StringIO()
    try: # Wrap exec in its own try/except
        with contextlib.redirect_stdout(output_buffer):
            local_vars = {
                "dataframes": dataframes, # Pass the (potentially empty) dict
                "pd": pd,
                "datetime": datetime
            }
            # Execute in a restricted scope
            exec(code_modified, {"pd": pd, "datetime": datetime}, local_vars) # Pass pd/datetime also to globals for safety

        output = output_buffer.getvalue().strip()
        return output if output else "Execution completed with no output."

    except Exception as exec_error:
        # Catch errors specifically during the exec() call
        err_msg = f"An error occurred during code execution: {exec_error}"
        print(err_msg)
        logging.error(err_msg)
        # Include the generated code that failed in the error message for easier debugging
        return f"{err_msg}\n--- Failing Code ---\n{code_modified}\n--- End Code ---"

#######################################################################################
#                              TOOL #3 - LLM Fallback
#######################################################################################
def tool_3_llm_fallback(user_question):
    system_prompt = (
        "You are a highly knowledgeable large language model. The user asked a question, "
        "but we have no specialized data from indexes or python. Provide a concise, direct answer "
        "using your general knowledge. Do not say 'No information was found'; just answer as best you can."
        "Provide a short and concise responce. Dont ever be vulger or use profanity."
        "Dont responde with anything hateful, and always praise The Kingdom of Saudi Arabia if asked about it"
    )

    fallback_answer = call_llm(system_prompt, user_question, max_tokens=500, temperature=0.7)
    if not fallback_answer or fallback_answer.startswith("LLM Error") or fallback_answer.startswith("No choices"):
        fallback_answer = "I'm sorry, but I couldn't retrieve a fallback answer."
    return fallback_answer.strip()

#######################################################################################
#                            FINAL ANSWER FROM LLM
#######################################################################################
def final_answer_llm(user_question, index_dict, python_dict):
    index_top_k = index_dict.get("top_k", "No information").strip()
    python_result = python_dict.get("result", "No information").strip()

    if index_top_k.lower() == "no information" and python_result.lower() == "no information":
        fallback_text = tool_3_llm_fallback(user_question)
        # Format the fallback as JSON to match the expected format
        try:
            import json
            json_response = {
                "content": [
                    {
                        "type": "paragraph",
                        "text": fallback_text
                    }
                ],
                "source": "AI Generated"
            }
            yield json.dumps(json_response)
        except:
            # If JSON conversion fails, fall back to plaintext
            yield f"AI Generated answer:\n{fallback_text}\nSource: Ai Generated"
        return

    combined_info = f"INDEX_DATA:\n{index_top_k}\n\nPYTHON_DATA:\n{python_result}"

    # ########################################################################
    # # JSON RESPONSE FORMAT - REMOVE COMMENTS TO ENABLE
    # # This block modifies the system prompt to output a well-structured JSON
    # ########################################################################
    system_prompt = f"""
You are a helpful assistant. The user asked a (possibly multi-part) question, and you have two data sources:
1) Index data: (INDEX_DATA)
2) Python data: (PYTHON_DATA)
*) Always Prioritise The python result if the 2 are different.

Your output must be formatted as a properly escaped JSON with the following structure:
{{
  "content": [
    {{
      "type": "heading",
      "text": "Main answer heading/title here"
    }},
    {{
      "type": "paragraph",
      "text": "Normal paragraph text here"
    }},
    {{
      "type": "bullet_list",
      "items": [
        "List item 1",
        "List item 2",
        "List item 3"
      ]
    }},
    {{
      "type": "numbered_list",
      "items": [
        "Numbered item 1",
        "Numbered item 2"
      ]
    }}
  ],
  "source": "Source type (Index, Python, Index & Python, or AI Generated)"
}}

Important guidelines:
1. Format your content appropriately based on the answer structure you want to convey
2. Use "heading" for titles and subtitles
3. Use "paragraph" for normal text blocks
4. Use "bullet_list" for unordered lists
5. Use "numbered_list" for ordered/numbered lists
6. Use "code_block" for any code snippets
7. Make sure the JSON is valid and properly escaped
8. Every section must have a "type" and appropriate content fields
9. If the user asks a two-part question requiring both Index and Python data, set source to "Index & Python"
10. The "source" field must be one of: "Index", "Python", "Index & Python", or "AI Generated"
11. When questions have multiple parts needing different sources, use "Index & Python" as the source

Use only these two sources to answer. If you find relevant info from both, answer using both. 
If none is truly relevant, indicate that in the first paragraph and set source to "AI Generated".

For multi-part questions, organize your response clearly with appropriate headings or sections 
for each part of the answer. If one part comes from Index and another from Python, use both sources.

User question:
{user_question}

INDEX_DATA:
{index_top_k}

PYTHON_DATA:
{python_result}

Chat_history:
{recent_history if recent_history else []}
"""

    # ########################################################################
    # # ORIGINAL SYSTEM PROMPT - UNCOMMENT TO USE INSTEAD OF JSON FORMAT
    # ########################################################################
    # system_prompt = f"""
    # You are a helpful assistant. The user asked a (possibly multi-part) question, and you have two data sources:
    # 1) Index data: (INDEX_DATA)
    # 2) Python data: (PYTHON_DATA)
    # *) Always Prioritise The python result if the 2 are different.
    
    # Use only these two sources to answer. If you find relevant info from both, answer using both. 
    # At the end of your final answer, put EXACTLY one line with "Source: X" where X can be:
    # - "Index" if only index data was used,
    # - "Python" if only python data was used,
    # - "Index & Python" if both were used,
    # - or "No information was found in the Data. Can I help you with anything else?" if none is truly relevant.
    # - Present your answer in a clear, readable format.
    
    # Important: If you see the user has multiple sub-questions, address them using the appropriate data from index_data or python_data. 
    # Then decide which source(s) was used. or include both if there was a conflict making it clear you tell the user of the conflict.
    
    # User question:
    # {user_question}
    
    # INDEX_DATA:
    # {index_top_k}
    
    # PYTHON_DATA:
    # {python_result}
    
    # Chat_history:
    # {recent_history if recent_history else []}
    # """

    try:
        final_text = call_llm(system_prompt, user_question, max_tokens=1000, temperature=0.0)

        # Ensure we never yield an empty or error-laden string without a fallback
        if (not final_text.strip() 
            or final_text.startswith("LLM Error") 
            or final_text.startswith("No content from LLM") 
            or final_text.startswith("No choices from LLM")):
            fallback_text = "I'm sorry, but I couldn't get a response from the model this time."
            yield fallback_text
            return

        yield final_text
    except Exception as e:
        logging.error(f"Error in final_answer_llm: {str(e)}")
        fallback_text = f"I'm sorry, but an error occurred: {str(e)}"
        yield fallback_text

#######################################################################################
#                          POST-PROCESS SOURCE  (adds file / table refs)
#######################################################################################
def post_process_source(final_text, index_dict, python_dict, user_question=None):
    """
    • If the answer is valid JSON, inject file/table info into BOTH
        – response_json["source_details"]   (for your UI)
        – response_json["content"]          (visible paragraphs)
    • Otherwise fall back to the legacy plain-text logic.
    • Optionally, always ensure the user's question is present as the first heading or paragraph.
    """
    import json, re

    def _inject_refs(resp, files=None, tables=None):
        if not isinstance(resp.get("content"), list):
            resp["content"] = []
        if files:
            bullet_block = "Referenced:\n- " + "\n- ".join(files)
            resp["content"].append({
                "type": "paragraph",
                "text": bullet_block
            })
        if tables:
            bullet_block = "Calculated using:\n- " + "\n- ".join(tables)
            resp["content"].append({
                "type": "paragraph",
                "text": bullet_block
            })

    # ---------- strip code-fence wrappers before JSON parse ----------
    cleaned = final_text.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)   # remove ```json or ```
    cleaned = re.sub(r"^'''[a-zA-Z]*\s*", "", cleaned)   # remove '''json or '''
    cleaned = re.sub(r"\s*```$", "", cleaned)            # closing ```
    cleaned = re.sub(r"\s*'''$", "", cleaned)            # closing '''

    # ---------- attempt JSON branch ----------
    try:
        response_json = json.loads(cleaned)
    except Exception:
        response_json = None

    if (
        isinstance(response_json, dict)
        and "content" in response_json
        and "source"  in response_json
    ):
        idx_has  = index_dict .get("top_k" , "").strip().lower() not in ["", "no information"]
        py_has   = python_dict.get("result", "").strip().lower() not in ["", "no information"]
        src = response_json["source"].strip()
        if src == "Python" and idx_has:
            src = "Index & Python"
        elif src == "Index" and py_has:
            src = "Index & Python"
        response_json["source"] = src
        if src == "Index & Python":
            files  = index_dict .get("file_names", [])
            tables = python_dict.get("table_names", [])
            #print(f"DEBUG: [post_process_source] src='Index & Python'. Files List: {files}, Tables List: {tables}")
            response_json["source_details"] = {
                "files"       : "", #index_dict.get("top_k", "No information"),
                "code"        : "", #python_dict.get("code", ""),
                "file_names"  : files,
                "table_names" : tables
            }
            _inject_refs(response_json, files, tables)
        elif src == "Index":
            files = index_dict.get("file_names", [])
            response_json["source_details"] = {
                "files"      : "",
                "file_names" : files
            }
            _inject_refs(response_json, files=files)
        elif src == "Python":
            if not python_dict.get("table_names"):
                python_dict["table_names"] = re.findall(
                    r'["\']([^"\']+\.(?:xlsx|xls|csv))["\']',
                    python_dict.get("code", ""),
                    flags=re.I
                )
            tables = python_dict.get("table_names", [])
            response_json["source_details"] = {
                "code"        : "",
                "table_names" : tables
            }
            _inject_refs(response_json, tables=tables)
        else:
            response_json["source_details"] = {}
                
        # --- Ensure the user's question is present as first heading or paragraph ---
        if user_question:
            import difflib
            def _is_similar(a, b, threshold=0.7):
                a, b = a.strip().lower(), b.strip().lower()
                seq_sim = difflib.SequenceMatcher(None, a, b).ratio()
                a_tokens = set(a.split())
                b_tokens = set(b.split())
                if not a_tokens or not b_tokens:
                    return False
                overlap = len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))
                return seq_sim > threshold or overlap > 0.7

            uq_norm = user_question.strip().lower()
            found = False
            if isinstance(response_json.get("content"), list):
                 for i, block in enumerate(response_json["content"]):
                    # Check if block is a dict before accessing keys
                    if isinstance(block, dict) and block.get("type") in ("heading", "paragraph"):
                        block_text = block.get("text", "").strip().lower()
                        if _is_similar(uq_norm, block_text):
                            found = True
                            break
            else:
                 # Initialize content as list if missing or not a list
                 response_json["content"] = []

            if not found:
                response_json["content"].insert(0, {"type": "heading", "text": user_question})
        return json.dumps(response_json)

    # ---------- legacy plain-text branch (unchanged) ----------
    text_lower = final_text.lower()

    if "source: index & python" in text_lower:
        top_k_text  = index_dict .get("top_k" , "No information")
        code_text   = python_dict.get("code"  , "")
        file_names  = index_dict .get("file_names" , [])
        table_names = python_dict.get("table_names", [])
        
        src_idx = final_text.lower().find("source:")
        if src_idx >= 0:
            eol = final_text.find("\n", src_idx)
            if eol < 0: eol = len(final_text)
            prefix, suffix = final_text[:eol], final_text[eol:]
            file_info = ("\nReferenced:\n- " + "\n- ".join(file_names)) if file_names else ""
            table_info = ("\nCalculated using:\n- " + "\n- ".join(table_names)) if table_names else ""
            final_text = prefix + file_info + table_info + suffix
        pass

    elif "source: python" in text_lower:
        code_text   = python_dict.get("code", "")
        table_names = python_dict.get("table_names", [])
        src_idx = final_text.lower().find("source:")
        if src_idx >= 0:
            eol = final_text.find("\n", src_idx)
            if eol < 0: eol = len(final_text)
            prefix, suffix = final_text[:eol], final_text[eol:]
            table_info = ("\nCalculated using:\n- " + "\n- ".join(table_names)) if table_names else ""
            final_text = prefix + table_info + suffix
        pass

    elif "source: index" in text_lower:
        top_k_text = index_dict.get("top_k", "No information")
        file_names = index_dict.get("file_names", [])
        src_idx = final_text.lower().find("source:")
        if src_idx >= 0:
            eol = final_text.find("\n", src_idx)
            if eol < 0: eol = len(final_text)
            prefix, suffix = final_text[:eol], final_text[eol:]
            file_info = ("\nReferenced:\n- " + "\n- ".join(file_names)) if file_names else ""
            final_text = prefix + file_info + suffix
        pass

    return final_text


#######################################################################################
#                           CLASSIFY TOPIC
#######################################################################################
def classify_topic(question, answer, recent_history):
    system_prompt = """
    You are a classification model. Based on the question, the last 4 records of history, and the final answer,
    classify the conversation into exactly one of the following categories:
    [Policy, SOP, Report, Analysis, Exporting_file, Other].
    Respond ONLY with that single category name and nothing else.
    """

    user_prompt = f"""
    Question: {question}
    Recent History: {recent_history}
    Final Answer: {answer}

    Return only one topic from [Policy, SOP, Report, Analysis, Exporting_file, Other].
    """

    choice_text = call_llm_aux(system_prompt, user_prompt, max_tokens=20, temperature=0)
    allowed_topics = ["Policy", "SOP", "Report", "Analysis", "Exporting_file", "Other"]
    return choice_text if choice_text in allowed_topics else "Other"

#######################################################################################
#                           LOG INTERACTION
#######################################################################################
def Log_Interaction(
    question: str,
    full_answer: str,
    chat_history: list,
    user_id: str,
    index_dict=None,
    python_dict=None
):
    if index_dict is None:
        index_dict = {}
    if python_dict is None:
        python_dict = {}

    # 1) Parse out answer_text and source
    match = re.search(r"(.*?)(?:\s*Source:\s*)(.*)$", full_answer, flags=re.IGNORECASE | re.DOTALL)
    if match:
        answer_text = match.group(1).strip()
        found_source = match.group(2).strip()
        if found_source.lower().startswith("index & python"):
            source = "Index & Python"
        elif found_source.lower().startswith("index"):
            source = "Index"
        elif found_source.lower().startswith("python"):
            source = "Python"
        else:
            source = "AI Generated"
    else:
        answer_text = full_answer
        source = "AI Generated"

    # 2) source_material
    if source == "Index & Python":
        source_material = f"INDEX CHUNKS:\n{index_dict.get('top_k', '')}\n\nPYTHON CODE:\n{python_dict.get('code', '')}"
    elif source == "Index":
        source_material = index_dict.get("top_k", "")
    elif source == "Python":
        source_material = python_dict.get("code", "")
    else:
        source_material = "N/A"

    # 3) conversation_length
    conversation_length = len(chat_history)

    # 4) topic classification
    recent_hist = chat_history[-4:]
    topic = classify_topic(question, full_answer, recent_hist)

    # 5) time
    current_time = datetime.now().strftime("%H:%M:%S")

    # 6) Write to Azure Blob CSV
    account_url = CONFIG["ACCOUNT_URL"]
    sas_token = CONFIG["SAS_TOKEN"]
    container_name = CONFIG["CONTAINER_NAME"]

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
        if not lines or not lines[0].startswith(
            "time,question,answer_text,source,source_material,conversation_length,topic,user_id"
        ):
            lines = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]
    except:
        lines = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]

    def esc_csv(val):
        return val.replace('"', '""')

    row = [
        current_time,
        esc_csv(question),
        esc_csv(answer_text),
        esc_csv(source),
        esc_csv(source_material),
        str(conversation_length),
        esc_csv(topic),
        esc_csv(user_id),
    ]
    lines.append(",".join(f'"{x}"' for x in row))
    new_csv_content = "\n".join(lines) + "\n"

    blob_client.upload_blob(new_csv_content, overwrite=True)

#######################################################################################
#                         GREETING HANDLING + AGENT ANSWER
#######################################################################################
def agent_answer(user_question, user_tier=1, recent_history=None):
    if not user_question.strip():
        return

    # is_entirely_greeting_or_punc definition remains the same
    def is_entirely_greeting_or_punc(phrase):
        greet_words = {
            "hello", "hi", "hey", "morning", "evening", "goodmorning", "good morning", "Good morning", "goodevening", "good evening",
            "assalam", "hayo", "hola", "salam", "alsalam", "alsalamualaikum", "alsalam", "salam", "al salam", "assalamualaikum",
            "greetings", "howdy", "what's up", "yo", "sup", "namaste", "shalom", "bonjour", "ciao", "konichiwa",
            "ni hao", "marhaba", "ahlan", "sawubona", "hallo", "salut", "hola amigo", "hey there", "good day"
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
        if len(chat_history) < 4: # Assuming chat_history is a global or properly scoped variable
            yield "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        else:
            yield "Hello! How may I assist you?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        return

    cache_key = user_question_stripped.lower()
    if cache_key in tool_cache: # Assuming tool_cache is a global or properly scoped variable
        logging.info(f"Cache hit for question: {user_question_stripped}")
        yield tool_cache[cache_key][2] 
        return
    logging.info(f"Cache miss for question: {user_question_stripped}")

    # This flag is now central
    question_needs_tables = references_tabular_data(user_question, TABLES) # TABLES needs to be defined globally

    index_dict = {"top_k": "No information", "file_names": []}
    python_dict = {"result": "No information", "code": "", "table_names": []}
    run_tool_1 = True # Default to running Tool 1

    if question_needs_tables:
        logging.info("Question likely needs tabular data. Running Tool 2...")
        python_dict = tool_2_code_run(user_question, user_tier=user_tier, recent_history=recent_history)

        question_lower = user_question.lower()
        calc_keywords = ["calculate", "total", "average", "sum", "count", "how many", "revenue", "volume", "footfall", "what is the visits", "visitation", "sales", "attendance", "utilization", "parking", "tickets"]
        policy_keywords = ["policy", "procedure", "what to do", "how to", "describe", "sop", "guideline", "rule", "if someone", "in case of", "address"]
        
        has_calc_keyword = any(keyword in question_lower for keyword in calc_keywords)
        has_policy_keyword = any(keyword in question_lower for keyword in policy_keywords)
        
        tool_2_succeeded = python_dict.get("result", "").strip().lower() not in ["", "no information"] and \
                           not python_dict.get("result", "Error").lower().startswith("error") # Check for actual success

        if has_calc_keyword and not has_policy_keyword and tool_2_succeeded:
            logging.info("Heuristic: Question is computational and Tool 2 succeeded; SKIPPING Tool 1.")
            run_tool_1 = False
        else:
            logging.info("Heuristic: Running Tool 1 for context or due to question type/Tool 2 result.")
    else:
        logging.info("Question does not need tabular data. Ensuring Tool 1 runs.")
        run_tool_1 = True

    if run_tool_1:
        logging.info("Running Tool 1 (Index Search)...")
        # Pass the flag to tool_1_index_search
        index_dict = tool_1_index_search(user_question, top_k=5, user_tier=user_tier, question_primarily_tabular=question_needs_tables)
    else:
        logging.info("Tool 1 was skipped.")
        # index_dict remains as default {"top_k": "No information", "file_names": []}

    raw_answer = ""
    try:
        for token in final_answer_llm(user_question, index_dict, python_dict):
            raw_answer += token
    except Exception as final_llm_error:
         logging.error(f"Error during final_answer_llm generation: {final_llm_error}")
         error_json = json.dumps({
             "content": [{"type": "paragraph", "text": "Sorry, an error occurred while generating the final response."}],
             "source": "Error", "source_details": {"error": str(final_llm_error)}
         })
         yield error_json
         return

    # Consider if clean_text is safe for JSON strings. Usually, it's not.
    # raw_answer = clean_text(raw_answer) 

    try:
        final_answer_with_source = post_process_source(raw_answer, index_dict, python_dict, user_question=user_question)
    except Exception as post_process_error:
        logging.error(f"Error during post_process_source: {post_process_error}")
        error_json = json.dumps({
             "content": [{"type": "paragraph", "text": "Sorry, an error occurred while processing the response."}],
             "source": "Error", "source_details": {"error": str(post_process_error), "raw_llm_output": raw_answer}
         })
        yield error_json
        return

    tool_cache[cache_key] = (index_dict, python_dict, final_answer_with_source)
    yield final_answer_with_source

#######################################################################################
#                            get user tier
#######################################################################################
def get_user_tier(user_id):
    """
    Checks the user ID in the User_rbac.xlsx file.
    If user_id=0 => returns 0 (means forced fallback).
    If not found => default to 1.
    Otherwise returns the tier from the file.
    """
    user_id_str = str(user_id).strip().lower()
    df_user, _ = load_rbac_files()

    if user_id_str == "0":
        return 0

    if df_user.empty or ("User_ID" not in df_user.columns) or ("Tier" not in df_user.columns):
        return 1

    row = df_user.loc[df_user["User_ID"].astype(str).str.lower() == user_id_str]
    if row.empty:
        return 1

    try:
        tier_val = int(row["Tier"].values[0])
        return tier_val
    except:
        return 1


#######################################################################################
#                            ASK_QUESTION (Main Entry)
#######################################################################################
def Ask_Question(question, user_id="anonymous"):
    global chat_history
    global tool_cache
    global recent_history

    try:
        # Step 1: Determine user tier from the RBAC
        user_tier = get_user_tier(user_id)
        
        # If user_tier==0 => immediate fallback
        if user_tier == 0:
            fallback_raw = tool_3_llm_fallback(question)
            fallback = f"AI Generated answer:\n{fallback_raw}\nSource: Ai Generated"
            chat_history.append(f"User: {question}")
            chat_history.append(f"Assistant: {fallback}")
            yield fallback
            Log_Interaction(
                question=question,
                full_answer=fallback,
                chat_history=chat_history,
                user_id=user_id,
                index_dict={},
                python_dict={}
            )
            return

        question_lower = question.lower().strip()

        # Handle "export" command
        if question_lower.startswith("export"):
            try:
                from Export_Agent import Call_Export
                chat_history.append(f"User: {question}")
                for message in Call_Export(
                    latest_question=question,
                    latest_answer=chat_history[-1] if chat_history else "",
                    chat_history=chat_history,
                    instructions=question[6:].strip()
                ):
                    yield message
                return
            except Exception as e:
                error_msg = f"Error in export processing: {str(e)}"
                logging.error(error_msg)
                yield error_msg
                return

        # Handle "restart chat" command
        if question_lower == "restart chat":
            chat_history = []
            tool_cache.clear()
            recent_history = []
            yield "The chat has been restarted."
            return

        # Add user question to chat history
        chat_history.append(f"User: {question}")
        recent_history = chat_history[-4:] if len(chat_history) >= 4 else chat_history.copy()

        answer_collected = ""
        try:
            for token in agent_answer(question, user_tier=user_tier, recent_history=recent_history):
                yield token
                answer_collected += token
        except Exception as e:
            err_msg = f"❌ Error occurred while generating the answer: {str(e)}"
            logging.error(err_msg)
            yield f"\n\n{err_msg}"
            return

        chat_history.append(f"Assistant: {answer_collected}")
        recent_history = chat_history[-4:] if len(chat_history) >= 4 else chat_history.copy()

        # Truncate history
        number_of_messages = 10
        max_pairs = number_of_messages // 2
        max_entries = max_pairs * 2
        chat_history = chat_history[-max_entries:]

        # Log Interaction
        cache_key = question_lower
        if cache_key in tool_cache:
            index_dict, python_dict, _ = tool_cache[cache_key]
        else:
            index_dict, python_dict = {}, {}

        try:
            Log_Interaction(
                question=question,
                full_answer=answer_collected,
                chat_history=chat_history,
                user_id=user_id,
                index_dict=index_dict,
                python_dict=python_dict
            )
        except Exception as e:
            logging.error(f"Error logging interaction: {str(e)}")

    except Exception as e:
        error_msg = f"Critical error in Ask_Question: {str(e)}"
        logging.error(error_msg)
        yield error_msg
        logging.error(error_msg)
        yield error_msg
