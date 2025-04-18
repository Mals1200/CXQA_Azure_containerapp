# Version 19
# 1. Centralized, Cached LLM Calls
# Old: Every time you called the LLM you spun up a fresh HTTP request with no built‑in caching or back‑off.
# New:
# Wrapped call_llm in both an LRU cache and tenacity retry (with exponential back‑off).
# Benefits:
# Repeated prompts hit the cache → zero extra network cost and near‑instant responses.
# Transient network hiccups are retried automatically, smoothing out flakes.
# 2. Persistent Sessions & Thread‑Safe Clients
# Old: Created new requests.Session(), BlobServiceClient and SearchClient inside helpers each time.
# New:
# One global _requests_session, _blob_service_client and _search_client are initialized once at module load.
# Protected shared caches with a Lock.
# Benefits:
# Re‑uses TCP connections → lower latency and less CPU.
# Thread‑safe access when you kick off multiple concurrent searches.
# 3. Concurrent Index Searches
# Old: Searched subquestions serially.
# New:
# Uses ThreadPoolExecutor to fire off up to 4 parallel semantic searches.
# Benefits:
# Substantial speed‑up when your question splits into multiple parts.
# 4. Unified Text‑Cleaning Pipeline
# Old: Scattered individual regex calls in different spots.
# New:
# A single clean_text() function with a compiled list of patterns.
# Benefits:
# Dramatically reduces regex‑compilation overhead.
# Easier to extend or tweak (all rules in one place).
# 5. Smarter RBAC Helpers with LRU Cache
# Old: load_rbac_files() was called over and over, loading Excel blobs each time.
# New:
# Decorated load_rbac_files() with @lru_cache(maxsize=1).
# Fuzzy‑matching logic factored into a tight get_file_tier() using SequenceMatcher.
# Benefits:
# Only downloads RBAC tables once per process → huge I/O win.
# Clearer, more maintainable fuzzy logic.
# 6. Enhanced Retry Strategies
# Old: Used only wait_fixed(2) for all retries.
# New:
# Mix of fixed and exponential delays, tuned per use‐case (call_llm vs. tool_1_index_search vs. tool_2_code_run).
# Benefits:
# Balances speed on quick retries with space for longer back‑off when needed.
# 7. Better Function Signatures & Constants
# Old: references_tabular_data hard‑coded recent history, mismatched param names, and you had to pass "".
# New:
# Clear, consistent argument names (tables_text) and always pass your TABLES constant.
# All schema/text constants (TABLES, SCHEMA_TEXT, SAMPLE_TEXT) defined once at top.
# Benefits:
# Eliminates silent bugs from empty parameters.
# Easier to see and update your data‑model documentation.
# Overall Impact
# Area	Old Version	New Version	Benefit
# LLM calls	No cache, fixed retries	LRU cache, exp. back‑off	Speed, reliability
# Index search	Serial	Concurrent	Throughput ↑
# Session management	Re‑init per call	Persistent, thread‑safe clients	Latency ↓, stability ↑
# Text cleaning	Multiple inline regex	Single pipeline	Maintainability, perf ↑
# RBAC loading	Re‑download on each call	Cached once	I/O ↓, perf ↑
# Retry strategies	Fixed delays	Mixed fixed & exponential	Resilience ↑
# API & constant wiring	Some mismatches, ad‑hoc passing	Unified, named constants	Fewer bugs, easier to read & modify
# ───────────────────────────────────────────────────────────────────
import os
import io
import re
import json
import csv
import logging
import warnings
import requests
import contextlib
import pandas as pd
from collections import OrderedDict
from io import BytesIO, StringIO
from datetime import datetime
from threading import Lock
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed
from difflib import SequenceMatcher

from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

# ========================================================================================
#                               GLOBAL CONFIG / CONSTANTS
# ========================================================================================
CONFIG = {
    "LLM_ENDPOINT": (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview"
    ),
    "LLM_API_KEY": "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor",
    "SEARCH_SERVICE_NAME": "cxqa-azureai-search",
    "SEARCH_ENDPOINT": "https://cxqa-azureai-search.search.windows.net",
    "ADMIN_API_KEY": "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv",
    "INDEX_NAME": "vector-1741865904949",
    "SEMANTIC_CONFIG_NAME": "vector-1741865904949-semantic-configuration",
    "CONTENT_FIELD": "chunk",
    "ACCOUNT_URL": "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "SAS_TOKEN": (
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D"
    ),
    "CONTAINER_NAME": "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore",
    "TARGET_FOLDER_PATH": "UI/2024-11-20_142337_UTC/cxqa_data/tabular/"
}


# Persistent HTTP session and Azure clients
_requests_session = requests.Session()
_blob_service_client = BlobServiceClient(
    account_url=CONFIG["ACCOUNT_URL"], credential=CONFIG["SAS_TOKEN"]
)
_search_client = SearchClient(
    endpoint=CONFIG["SEARCH_ENDPOINT"],
    index_name=CONFIG["INDEX_NAME"],
    credential=AzureKeyCredential(CONFIG["ADMIN_API_KEY"])
)


# ========================================================================================
#                           TABLES / SCHEMA / SAMPLE GENERATION
# ========================================================================================
@lru_cache(maxsize=1)
def load_table_metadata(sample_n: int = 2):
    """
    Returns OrderedDict:
      filename -> {"schema": {col: dtype_str}, "sample": [row dicts ≤ sample_n]}
    """
    container = _blob_service_client.get_container_client(CONFIG["CONTAINER_NAME"])
    prefix    = CONFIG["TARGET_FOLDER_PATH"]
    meta      = OrderedDict()

    for blob in container.list_blobs(name_starts_with=prefix):
        fn = os.path.basename(blob.name)
        if not fn.lower().endswith((".xlsx", ".xls", ".csv")):
            continue

        data = container.get_blob_client(blob.name).download_blob().readall()
        df   = (pd.read_excel if fn.lower().endswith((".xlsx", ".xls"))
                else pd.read_csv)(BytesIO(data))

        schema = {col: str(dt) for col, dt in df.dtypes.items()}
        sample = df.head(sample_n).to_dict(orient="records")
        meta[fn] = {"schema": schema, "sample": sample}

    return meta

def format_tables_text(meta: dict) -> str:
    """Builds the TABLES string (schema only)."""
    lines = []
    for i, (fn, info) in enumerate(meta.items(), 1):
        lines.append(f'{i}) "{fn}", with the following tables:')
        for col, dt in info["schema"].items():
            lines.append(f"   -{col}: {dt}")
    return "\n".join(lines)

def format_schema_and_sample(
    meta: dict,
    sample_n: int = 2,
    char_limit: int = 15
) -> str:
    """Builds SCHEMA_TEXT with schema + truncated samples."""
    def truncate_val(v):
        s = "" if v is None else str(v)
        return s if len(s) <= char_limit else s[:char_limit] + "…"

    lines = []
    for fn, info in meta.items():
        # schema line
        lines.append(f"{fn}: {info['schema']}")
        # sample rows
        truncated = [
            {col: truncate_val(val) for col, val in row.items()}
            for row in info["sample"][:sample_n]
        ]
        lines.append(f"    Sample: {truncated},")
    return "\n".join(lines)

# Generate constants at startup
_metadata   = load_table_metadata(sample_n=2)
TABLES      = format_tables_text(_metadata)
SCHEMA_TEXT = format_schema_and_sample(_metadata, sample_n=2, char_limit=15)




# Silence verbose Azure logs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)



# Thread‑safe cache and history
_tool_cache = {}
_cache_lock = Lock()
chat_history = []

#######################################################################################
#                           RBAC HELPERS (User & File Tiers)
#######################################################################################
@lru_cache(maxsize=1)
def load_rbac_files():
    """
    Loads User_rbac.xlsx and File_rbac.xlsx from Azure Blob Storage.
    Returns two DataFrames: df_user, df_file.
    """
    df_user = pd.DataFrame()
    df_file = pd.DataFrame()
    try:
        container = _blob_service_client.get_container_client(CONFIG["CONTAINER_NAME"])
        base = "UI/2024-11-20_142337_UTC/cxqa_data/RBAC/"
        # User RBAC
        blob = container.get_blob_client(base + "User_rbac.xlsx")
        df_user = pd.read_excel(BytesIO(blob.download_blob().readall()))
        # File RBAC
        blob = container.get_blob_client(base + "File_rbac.xlsx")
        df_file = pd.read_excel(BytesIO(blob.download_blob().readall()))
    except Exception as e:
        logging.error(f"Failed to load RBAC files: {e}")
    return df_user, df_file

def get_user_tier(user_id: str) -> int:
    """
    Checks the user ID in User_rbac.xlsx.
    user_id="0" => tier 0 (forced fallback), not found => tier 1.
    """
    uid = str(user_id).strip().lower()
    if uid == "0":
        return 0
    df_user, _ = load_rbac_files()
    if df_user.empty or "User_ID" not in df_user.columns or "Tier" not in df_user.columns:
        return 1
    row = df_user[df_user["User_ID"].astype(str).str.lower() == uid]
    try:
        return int(row["Tier"].values[0]) if not row.empty else 1
    except:
        return 1

def get_file_tier(file_name: str) -> int:
    """
    Fuzzy‑matches file_name against File_rbac.xlsx to find required tier.
    Default tier=1 if no good match.
    """
    _, df_file = load_rbac_files()
    if df_file.empty or "File_Name" not in df_file.columns or "Tier" not in df_file.columns:
        return 1
    base = re.sub(r"\.(pdf|xlsx?|csv)$", "", file_name, flags=re.IGNORECASE).lower().strip()
    best_ratio, best_tier = 0.0, 1
    for _, row in df_file.iterrows():
        fn = str(row["File_Name"])
        compare = re.sub(r"\.(pdf|xlsx?|csv)$", "", fn, flags=re.IGNORECASE).lower().strip()
        ratio = SequenceMatcher(None, base, compare).ratio()
        tier = int(row["Tier"]) if str(row["Tier"]).isdigit() else 1
        if ratio > best_ratio:
            best_ratio, best_tier = ratio, tier
    return best_tier if best_ratio >= 0.8 else 1

#######################################################################################
#                   CENTRALIZED LLM CALL (with caching & back‑off)
#######################################################################################
@lru_cache(maxsize=512)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 500, temperature: float = 0.0) -> str:
    """
    Unified helper to call the Azure OpenAI endpoint, with retries and caching.
    """
    headers = {"Content-Type": "application/json", "api-key": CONFIG["LLM_API_KEY"]}
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    resp = _requests_session.post(CONFIG["LLM_ENDPOINT"], headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices", [])
    if choices:
        content = choices[0]["message"].get("content", "").strip()
        if content:
            return content
        logging.warning("LLM returned empty content.")
        return "No content from LLM."
    logging.warning(f"LLM returned no choices: {data}")
    return "No choices from LLM."

#######################################################################################
#                   COMBINED TEXT CLEANING (micro‑optimizations)
#######################################################################################
_clean_steps = [
    (re.compile(r'\b(\w+)( \1\b)+', re.IGNORECASE),      r'\1'),
    (re.compile(r'\b(\w{3,})\1\b', re.IGNORECASE),       r'\1'),
    (re.compile(r'\s{2,}'),                             ' '),
    (re.compile(r'\.{3,}'),                             '...'),
]
def clean_text(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _clean_steps:
        text = pattern.sub(repl, text)
    return text.strip()

# Deduplication helpers (unchanged)
def deduplicate_streaming_tokens(last_tokens, new_token):
    if last_tokens.endswith(new_token):
        return ""
    return new_token

def is_repeated_phrase(last_text, new_text, threshold=0.98):
    if not last_text or not new_text:
        return False
    comp_len = min(len(last_text), 100)
    recent = last_text[-comp_len:]
    return SequenceMatcher(None, recent, new_text).ratio() > threshold

#######################################################################################
#                           SUBQUESTION SPLITTING
#######################################################################################
def split_question_into_subquestions(user_question, use_semantic_parsing=True):
    if not user_question.strip():
        return []
    if not use_semantic_parsing:
        text = re.sub(r"\s+and\s+", " ~SPLIT~ ", user_question, flags=re.IGNORECASE)
        text = re.sub(r"\s*&\s*", " ~SPLIT~ ", text)
        return [p.strip() for p in text.split("~SPLIT~") if p.strip()]
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
    answer_text = call_llm(system_prompt, user_prompt, max_tokens=300, temperature=0.0)
    lines = [l.lstrip("•-0123456789). ").strip() for l in answer_text.split("\n") if l.strip()]
    return lines or [user_question]

#######################################################################################
#                 REFERENCES CHECK & RELEVANCE CHECK
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

    chat_history
    {chat_history[-4:]}

    Available Tables:
    {tables_text}

    Decision Rules:
    1. Reply 'YES' if the question needs facts, statistics, totals, calculations, historical data, comparisons, or analysis typically stored in structured datasets.
    2. Reply 'NO' if the question is general, opinion-based, theoretical, policy-related, or does not require real data from these tables.
    3. Completely ignore the sample rows of the tables. Assume full datasets exist beyond the samples.
    4. Be STRICT: only reply 'NO' if you are CERTAIN the tables are not needed.
    5. Do NOT create or assume data. Only decide if the tabular data is NEEDED to answer.
    6. Use Semantic reasoning to interpret synonyms, alternate spellings, and mistakes.

    Final instruction: Reply ONLY with 'YES' or 'NO'.
    """
    resp = call_llm(llm_system_message, llm_user_message, max_tokens=5, temperature=0.0).strip().upper()
    return resp == "YES"

def is_text_relevant(question, snippet):
    if not snippet.strip():
        return False
    system_prompt = (
        "You are a classifier. We have a user question and a snippet of text. "
        "Decide if the snippet is truly relevant to answering the question. "
        "Return ONLY 'YES' or 'NO'."
    )
    user_prompt = f"Question: {question}\nSnippet: {snippet}\nRelevant? Return 'YES' or 'NO' only."
    return call_llm(system_prompt, user_prompt, max_tokens=10, temperature=0.0).strip().upper() == "YES"

#######################################################################################
#                              TOOL #1 - Index Search
#######################################################################################
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5, user_tier=1):
    subquestions = split_question_into_subquestions(user_question, True)
    if not subquestions:
        subquestions = [user_question]

    all_docs = []
    with ThreadPoolExecutor(max_workers=min(4, len(subquestions))) as exe:
        futures = {exe.submit(
            _search_client.search,
            subq,
            query_type="semantic",
            semantic_configuration_name=CONFIG["SEMANTIC_CONFIG_NAME"],
            top=top_k,
            select=["title", CONFIG["CONTENT_FIELD"]],
            include_total_count=False
        ): subq for subq in subquestions}
        for fut in as_completed(futures):
            try:
                for r in fut.result():
                    snippet = r.get(CONFIG["CONTENT_FIELD"], "").strip()
                    title   = r.get("title", "").strip()
                    if snippet:
                        all_docs.append({"title": title, "snippet": snippet})
            except Exception:
                continue

    if not all_docs:
        return {"top_k": "No information"}

    relevant = []
    for doc in all_docs:
        if user_tier >= get_file_tier(doc["title"]):
            if is_text_relevant(user_question, doc["snippet"]):
                relevant.append(doc)

    if not relevant:
        return {"top_k": "No information"}

    for d in relevant:
        ttl = d["title"].lower()
        d["weight_score"] = ("policy" in ttl)*10 + ("report" in ttl)*5 + ("sop" in ttl)*3

    top_docs = sorted(relevant, key=lambda x: x["weight_score"], reverse=True)[:top_k]
    combined = "\n\n---\n\n".join(d["snippet"] for d in top_docs)
    return {"top_k": combined}

#######################################################################################
#                 HELPER to check table references vs. user tier
#######################################################################################
def reference_table_data(code_str, user_tier):
    pattern = re.compile(r'dataframes\.get\(\s*[\'"]([^\'"]+)[\'"]\s*\)')
    for fname in pattern.findall(code_str):
        req = get_file_tier(fname)
        if user_tier < req:
            return f"User does not have access to {fname} (requires tier {req})."
    return None

#######################################################################################
#                              TOOL #2 - Code Run
#######################################################################################
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def tool_2_code_run(user_question, user_tier=1):
    if not references_tabular_data(user_question, TABLES):
        return {"result": "No information", "code": ""}

    system_prompt = f"""
You are a python expert. Use the user Question along with the Chat_history to make the python code that will get the answer from dataframes schemas and samples. 
Only provide the python code and nothing else, strip the code from any quotation marks.
Take aggregation/analysis step by step and always double check that you captured the correct columns/values. 
Don't give examples, only provide the actual code. If you can't provide the code, say "404" and make sure it's a string.

**Rules**:
1. Only use columns that actually exist. Do NOT invent columns or table names.
2. Don’t rely on sample rows; the real dataset can have more data. Just reference the correct columns as shown in the schemas.
3. Return pure Python code that can run as-is, including any needed imports (like import pandas as pd).
4. The code must produce a final print statement with the answer.
5. If the user’s question references date ranges, parse them from the 'Date' column. If monthly data is requested, group by month or similar.
6. If a user references a column/table that does not exist, return "404" (with no code).
7. Use semantic reasoning to handle synonyms or minor typos (e.g., “Al Bujairy,” “albujairi,” etc.), as long as they reasonably map to the real table names.

User question:
{user_question}

Dataframes schemas and samples:
{SCHEMA_TEXT}

Chat_history:
{chat_history[-4:]}
"""
    code_str = call_llm(system_prompt, user_question, max_tokens=1200, temperature=0.7)
    if not code_str or code_str.strip() == "404":
        return {"result": "No information", "code": ""}

    if err := reference_table_data(code_str, user_tier):
        return {"result": err, "code": ""}

    # Load all dataframes once
    dfs = {}
    cont = _blob_service_client.get_container_client(CONFIG["CONTAINER_NAME"])
    for blob in cont.list_blobs(name_starts_with=CONFIG["TARGET_FOLDER_PATH"]):
        fn = os.path.basename(blob.name)
        data = cont.get_blob_client(blob.name).download_blob().readall()
        if fn.lower().endswith((".xlsx", ".xls")):
            dfs[fn] = pd.read_excel(BytesIO(data))
        elif fn.lower().endswith(".csv"):
            dfs[fn] = pd.read_csv(BytesIO(data))

    # Execute the code
    out_buf = StringIO()
    with contextlib.redirect_stdout(out_buf):
        local_vars = {"dataframes": dfs, "pd": pd, "datetime": datetime}
        exec(
            code_str
                .replace("pd.read_excel(", "dataframes.get(")
                .replace("pd.read_csv(",   "dataframes.get("),
            {},
            local_vars
        )
    result = out_buf.getvalue().strip() or "Execution completed with no output."
    return {"result": result, "code": code_str}

#######################################################################################
#                              TOOL #3 - LLM Fallback
#######################################################################################
def tool_3_llm_fallback(user_question):
    system_prompt = (
        "You are a highly knowledgeable large language model. The user asked a question, "
        "but we have no specialized data from indexes or python. Provide a concise, direct answer "
        "using your general knowledge. Do not say 'No information was found'; just answer as best you can. "
        "Provide a short and concise responce. Dont ever be vulger or use profanity. "
        "Dont responde with anything hateful, and always praise The Kingdom of Saudi Arabia if asked about it"
    )
    fallback = call_llm(system_prompt, user_question, max_tokens=500, temperature=0.7)
    if not fallback or fallback.startswith(("LLM Error", "No choices", "No content")):
        return "I'm sorry, but I couldn't retrieve a fallback answer."
    return fallback.strip()

#######################################################################################
#                            FINAL ANSWER FROM LLM
#######################################################################################
def final_answer_llm(user_question, index_dict, python_dict):
    index_top = index_dict.get("top_k", "").strip()
    py_res    = python_dict.get("result", "").strip()
    if index_top.lower() == "no information" and py_res.lower() == "no information":
        ans = tool_3_llm_fallback(user_question)
        yield f"AI Generated answer:\n{ans}\nSource: Ai Generated"
        return

    combined = f"INDEX_DATA:\n{index_top}\n\nPYTHON_DATA:\n{py_res}"
    system_prompt = f"""
You are a helpful assistant. The user asked a (possibly multi-part) question, and you have two data sources:
1) Index data: (INDEX_DATA)
2) Python data: (PYTHON_DATA)

Use only these two sources to answer. If you find relevant info from both, answer using both. 
At the end of your final answer, put EXACTLY one line with "Source: X" where X can be:
- "Index" if only index data was used,
- "Python" if only python data was used,
- "Index & Python" if both were used,
- or "No information was found in the Data. Can I help you with anything else?" if none is truly relevant.

Important: If you see the user has multiple sub-questions, address them using the appropriate data from index_data or python_data. 
Then decide which source(s) was used. or include both if there was a conflict making it clear you tell the user of the conflict.

User question:
{user_question}

INDEX_DATA:
{index_top}

PYTHON_DATA:
{py_res}

Chat_history:
{chat_history}
"""
    final_text = call_llm(system_prompt, user_question, max_tokens=1000, temperature=0.0)
    if not final_text.strip() or final_text.startswith(("LLM Error", "No content", "No choices")):
        yield "I’m sorry, but I couldn’t get a response from the model this time."
    else:
        yield final_text

#######################################################################################
#                          POST-PROCESS SOURCE
#######################################################################################
def post_process_source(final_text, index_dict, python_dict):
    low = final_text.lower()
    if "source: index & python" in low:
        return f"""{final_text}

The Files:
{index_dict.get("top_k", "")}

The code:
{python_dict.get("code", "")}
"""
    if "source: python" in low:
        return f"""{final_text}

The code:
{python_dict.get("code", "")}
"""
    if "source: index" in low:
        return f"""{final_text}

The Files:
{index_dict.get("top_k", "")}
"""
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
    choice = call_llm(system_prompt, user_prompt, max_tokens=20, temperature=0)
    return choice if choice in ["Policy","SOP","Report","Analysis","Exporting_file","Other"] else "Other"

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
    index_dict  = index_dict or {}
    python_dict = python_dict or {}

    # parse source
    m = re.search(r"(.*?)(?:\s*Source:\s*)(.*)$", full_answer, flags=re.IGNORECASE|re.DOTALL)
    if m:
        answer_text = m.group(1).strip()
        found_source = m.group(2).strip().lower()
        if found_source.startswith("index & python"):
            source = "Index & Python"
        elif found_source.startswith("index"):
            source = "Index"
        elif found_source.startswith("python"):
            source = "Python"
        else:
            source = "AI Generated"
    else:
        answer_text, source = full_answer, "AI Generated"

    if source == "Index & Python":
        source_material = f"INDEX CHUNKS:\n{index_dict.get('top_k','')}\n\nPYTHON CODE:\n{python_dict.get('code','')}"
    elif source == "Index":
        source_material = index_dict.get("top_k","")
    elif source == "Python":
        source_material = python_dict.get("code","")
    else:
        source_material = "N/A"

    conv_len = len(chat_history)
    topic = classify_topic(question, full_answer, chat_history[-4:])
    curr_time = datetime.now().strftime("%H:%M:%S")

    # write to Azure Blob CSV
    container = _blob_service_client.get_container_client(CONFIG["CONTAINER_NAME"])
    folder = "UI/2024-11-20_142337_UTC/cxqa_data/logs/"
    fname = f"logs_{datetime.now().strftime('%Y_%m_%d')}.csv"
    blob = container.get_blob_client(folder + fname)

    try:
        existing = blob.download_blob().readall().decode("utf-8")
        lines = existing.strip().split("\n")
        if not lines or not lines[0].startswith("time,question,answer_text,source,source_material,conversation_length,topic,user_id"):
            lines = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]
    except:
        lines = ["time,question,answer_text,source,source_material,conversation_length,topic,user_id"]

    def esc(v): return v.replace('"','""')
    row = [
        curr_time,
        esc(question),
        esc(answer_text),
        esc(source),
        esc(source_material),
        str(conv_len),
        esc(topic),
        esc(user_id),
    ]
    lines.append(",".join(f'"{x}"' for x in row))
    blob.upload_blob("\n".join(lines)+"\n", overwrite=True)

#######################################################################################
#                         GREETING HANDLING + AGENT ANSWER
#######################################################################################
def agent_answer(user_question, user_tier=1):
    if not user_question.strip():
        return

    def is_entirely_greeting_or_punc(phrase):
        greet = {
            "hello","hi","hey","morning","evening","goodmorning","good morning","goodevening","good evening",
            "assalam","hayo","hola","salam","alsalam","alsalamualaikum","greetings","howdy","what's up","yo","sup",
            "namaste","shalom","bonjour","ciao","konichiwa","ni hao","marhaba","ahlan","sawubona","hallo","salut","hola amigo","hey there","good day"
        }
        toks = re.findall(r"[A-Za-z']+", phrase.lower())
        return toks and all(t in greet for t in toks)

    q = user_question.strip()
    if is_entirely_greeting_or_punc(q):
        if len(chat_history) < 4:
            return "Hello! I'm The CXQA AI Assistant. I'm here to help you. What would you like to know today?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."
        else:
            return "Hello! How may I assist you?\n- To reset the conversation type 'restart chat'.\n- To generate Slides, Charts or Document, type 'export followed by your requirements."

    key = q.lower()
    with _cache_lock:
        if key in _tool_cache:
            return _tool_cache[key]

    needs_tab = references_tabular_data(user_question, TABLES)
    python_dict = tool_2_code_run(user_question, user_tier) if needs_tab else {"result":"No information","code":""}
    index_dict  = tool_1_index_search(user_question, top_k=5, user_tier=user_tier)

    raw_answer = ""
    for token in final_answer_llm(user_question, index_dict, python_dict):
        raw_answer += token

    raw_answer = clean_text(raw_answer)
    final = post_process_source(raw_answer, index_dict, python_dict)

    with _cache_lock:
        _tool_cache[key] = final

    return final

#######################################################################################
#                           ASK_QUESTION (Main Entry)
#######################################################################################
def Ask_Question(question, user_id="anonymous"):
    global chat_history
    tier = get_user_tier(user_id)

    # forced fallback
    if tier == 0:
        fb = tool_3_llm_fallback(question)
        ans = f"AI Generated answer:\n{fb}\nSource: Ai Generated"
        chat_history.append(f"User: {question}")
        chat_history.append(f"Assistant: {ans}")
        Log_Interaction(question, ans, chat_history, user_id)
        return ans

    # export handler
    if question.lower().startswith("export"):
        from Export_Agent import Call_Export
        chat_history.append(f"User: {question}")
        out = []
        for msg in Call_Export(
            latest_question=question,
            latest_answer=chat_history[-1] if chat_history else "",
            chat_history=chat_history,
            instructions=question[6:].strip()
        ):
            out.append(msg)
        return "\n".join(out)

    # restart chat
    if question.lower() == "restart chat":
        chat_history = []
        _tool_cache.clear()
        return "The chat has been restarted."

    # normal flow
    chat_history.append(f"User: {question}")
    try:
        answer = agent_answer(question, tier)
    except Exception as e:
        answer = f"❌ Error occurred while generating the answer: {e}"

    chat_history.append(f"Assistant: {answer}")
    # keep last 10 messages
    chat_history = chat_history[-10:]
    Log_Interaction(question, answer, chat_history, user_id)
    return answer

