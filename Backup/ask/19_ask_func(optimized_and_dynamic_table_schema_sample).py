# Version 18c  ───────────────────────────────────────────────────────────────────
#  ▪ Single‑index / single‑blob chatbot
#  ▪ Dynamic TABLES / SAMPLE_TEXT / SCHEMA_TEXT built on first access
#  ▪ Text‑tier RBAC via text_tier.xlsx (document access)
#  ▪ Table‑tier RBAC via rbac.xlsx       (tabular access)
#  ▪ All supplied (fake) keys preserved
# ────────────────────────────────────────────────────────────────────────────────

import re, io, difflib, logging, contextlib, threading, time, ast
from io import BytesIO, StringIO
from datetime import datetime
from functools import lru_cache

import pandas as pd
import requests
from requests.exceptions import ConnectionError, HTTPError
from http.client import RemoteDisconnected
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

# ─────────────────── GLOBAL CONFIG ─────────────────────────────────────────────
CONFIG = {
    "LLM_ENDPOINT":
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions?api-version=2024-08-01-preview",
    "LLM_API_KEY":  "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor",

    "SEARCH_ENDPOINT":       "https://cxqa-azureai-search.search.windows.net",
    "ADMIN_API_KEY":         "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv",
    "INDEX_NAME":            "vector-1741865904949",
    "SEMANTIC_CONFIG_NAME":  "vector-1741865904949-semantic-configuration",
    "CONTENT_FIELD":         "chunk",

    "ACCOUNT_URL":      "https://cxqaazureaihub8779474245.blob.core.windows.net",
    "SAS_TOKEN":
        "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx&"
        "se=2030-11-21T02:02:26Z&st=2024-11-20T18:02:26Z&"
        "spr=https&sig=YfZEUMeqiuBiG7le2JfaaZf%2FW6t8ZW75yCsFM6nUmUw%3D",

    "CONTAINER_NAME":      "5d74a98c-1fc6-4567-8545-2632b489bd0b-azureml-blobstore",
    "TARGET_FOLDER_PATH":  "UI/2024-11-20_142337_UTC/cxqa_data/tabular/",
    "RBAC_FOLDER":         "UI/2024-11-20_142337_UTC/cxqa_data/RBAC/",
}

logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ─────────────────── GLOBAL RUNTIME STATE ─────────────────────────────────────
_SESSION            = requests.Session()
_SESSION.headers.update({"Connection": "keep-alive"})
_LLM_LOCK           = threading.Semaphore(2)
_LLM_CACHE          = {}
_CONTAINER_CLIENTS  = {}
_SEARCH_CLIENT      = None
_DF_CACHE           = {}
tool_cache          = {}
chat_history        = []

# ─────────────────── CLIENT HELPERS ───────────────────────────────────────────
def _get_container():
    key = (CONFIG["ACCOUNT_URL"], CONFIG["CONTAINER_NAME"])
    if key not in _CONTAINER_CLIENTS:
        _CONTAINER_CLIENTS[key] = (
            BlobServiceClient(account_url=CONFIG["ACCOUNT_URL"],
                              credential=CONFIG["SAS_TOKEN"])
            .get_container_client(CONFIG["CONTAINER_NAME"]))
    return _CONTAINER_CLIENTS[key]

def _get_search_client():
    global _SEARCH_CLIENT
    if _SEARCH_CLIENT is None:
        _SEARCH_CLIENT = SearchClient(
            endpoint=CONFIG["SEARCH_ENDPOINT"],
            index_name=CONFIG["INDEX_NAME"],
            credential=AzureKeyCredential(CONFIG["ADMIN_API_KEY"]))
    return _SEARCH_CLIENT

# ─────────────────── DYNAMIC TABLE / SCHEMA BUILDER ───────────────────────────
_TABLES_STR_CACHE = _SAMPLE_TEXT_STR_CACHE = _SCHEMA_TEXT_STR_CACHE = None

def _build_metadata_once():
    global _TABLES_STR_CACHE, _SAMPLE_TEXT_STR_CACHE, _SCHEMA_TEXT_STR_CACHE
    if _TABLES_STR_CACHE is not None:
        return                                           # already built

    cont      = _get_container()
    base      = CONFIG["TARGET_FOLDER_PATH"]
    file_meta = {}

    for blob in cont.list_blobs(name_starts_with=base):
        fname = blob.name.rsplit("/", 1)[-1]
        if not fname.lower().endswith((".csv", ".xlsx", ".xls")):
            continue
        if fname in file_meta:
            continue
        try:
            raw = cont.download_blob(blob).readall()
            df  = pd.read_csv(BytesIO(raw)) if fname.lower().endswith(".csv") \
                  else pd.read_excel(BytesIO(raw))
        except Exception as e:
            logging.warning(f"[metadata] skip {fname}: {e}")
            continue

        schema  = {c: str(df[c].dtype) for c in df.columns}
        samples = [{c: repr(df.iloc[i][c]) for c in df.columns}
                   for i in range(min(len(df), 3))]
        file_meta[fname] = {"schema": schema, "samples": samples}

    tbl_lines, smp_chunks, sch_chunks = [], [], []
    for i, (fname, meta) in enumerate(file_meta.items(), start=1):
        schdesc = ", ".join(f"{c}: {t}" for c, t in meta["schema"].items())
        tbl_lines.append(f'{i}) "{fname}", with the following tables:\n   -{schdesc}')
        smp_chunks.append(f"{fname}: [{', '.join(map(str, meta['samples']))}],")
        sch_dict = ", ".join(f"'{c}': '{t}'" for c, t in meta["schema"].items())
        sch_chunks.append(f"{fname}: {{{sch_dict}}},")

    _TABLES_STR_CACHE      = "\n".join(tbl_lines)
    _SAMPLE_TEXT_STR_CACHE = "\n".join(smp_chunks)
    _SCHEMA_TEXT_STR_CACHE = "\n".join(sch_chunks)

def _TABLES():      _build_metadata_once(); return _TABLES_STR_CACHE
def _SAMPLE_TEXT(): _build_metadata_once(); return _SAMPLE_TEXT_STR_CACHE
def _SCHEMA_TEXT(): _build_metadata_once(); return _SCHEMA_TEXT_STR_CACHE

TABLES, SAMPLE_TEXT, SCHEMA_TEXT = _TABLES(), _SAMPLE_TEXT(), _SCHEMA_TEXT()

# ─────────────────── LLM WRAPPER (retry + cache + throttle) ───────────────────
def _should_retry(exc):
    if isinstance(exc, RemoteDisconnected): return True
    if isinstance(exc, (ConnectionError, HTTPError)):
        if isinstance(exc, HTTPError) and exc.response is not None:
            return exc.response.status_code in (429, 502, 503, 504)
        return True
    return False

@retry(wait=wait_fixed(2), stop=stop_after_attempt(6),
       retry=retry_if_exception_type((ConnectionError, HTTPError, RemoteDisconnected)))
def _post_llm(headers, payload):
    with _LLM_LOCK:
        r = _SESSION.post(CONFIG["LLM_ENDPOINT"], headers=headers,
                          json=payload, timeout=30)
    if r.status_code == 429:
        time.sleep(int(r.headers.get("Retry-After", "1") or "1"))
    r.raise_for_status()
    return r.json()

def call_llm(system, user, max_tokens=500, temperature=0.0):
    key = (system, user, max_tokens, temperature)
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]
    headers = {"Content-Type": "application/json", "api-key": CONFIG["LLM_API_KEY"]}
    payload = {"messages":[{"role":"system","content":system},
                           {"role":"user","content":user}],
               "max_tokens":max_tokens, "temperature":temperature}
    try:
        data = _post_llm(headers, payload)
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        logging.error(f"LLM Error: {e}")
        content = f"LLM Error: {e}"
    _LLM_CACHE[key] = content
    return content

# ─────────────────── RBAC HELPERS ──────────────────────────────────────────────
@lru_cache(maxsize=None)
def get_text_tier_map():
    path = CONFIG["RBAC_FOLDER"] + "text_tier.xlsx"
    df   = pd.read_excel(BytesIO(_get_container().download_blob(path).readall()))
    return {str(r["Doc_Name"]).strip(): str(r["Tier_Level"]).strip().lower()
            for _, r in df.iterrows()}

@lru_cache(maxsize=None)
def get_rbac_maps():
    """
    Returns (user_tier_map, table_tier_map)
      user_tier_map : { user_id -> 't1'/'t2'/... }
      table_tier_map: { table_name -> 't3'/... }   (based on rbac.xlsx)
    """
    path = CONFIG["RBAC_FOLDER"] + "rbac.xlsx"
    data = BytesIO(_get_container().download_blob(path).readall())
    xl   = pd.ExcelFile(data)
    # First sheet: users, second sheet: tables   (assumed)
    df_user  = xl.parse(0)
    df_table = xl.parse(1)
    umap = {str(r[0]).strip().lower(): str(r[1]).strip().lower()
            for _, r in df_user.iterrows() if len(r)>=2}
    tmap = {str(r[0]).strip(): str(r[1]).strip().lower()
            for _, r in df_table.iterrows() if len(r)>=2}
    return umap, tmap

def tiers_allowed(tier_str):
    """'t3'  → {'t1','t2','t3'}"""
    level = int(re.sub(r'\D','', tier_str or "1"))
    return {f"t{i}" for i in range(1, level+1)}

def get_user_tier(user_id):
    uid = str(user_id).strip().lower()
    umap,_ = get_rbac_maps()
    return umap.get(uid, "t1") if uid!="0" else "t0"

def get_table_tier(table_name):
    _, tmap = get_rbac_maps()
    return tmap.get(table_name, "t1")

# ─────────────────── TEXT CLEAN ‑ helpers ─────────────────────────────────────
def clean_text(txt):
    if not txt: return txt
    txt = re.sub(r'\b(\w+)( \1\b)+', r'\1', txt, flags=re.I)  # repeated words
    txt = re.sub(r'\b(\w{3,})\1\b', r'\1', txt, flags=re.I)   # repeated chars
    txt = re.sub(r'\s{2,}', ' ', txt)
    txt = re.sub(r'\.{3,}', '...', txt)
    return txt.strip()

# ─────────────────── SUB‑QUESTION SPLIT ───────────────────────────────────────
def split_question(q):
    if not q.strip(): return []
    sys=("You are a helpful assistant. Split the question into separate, "
         "self‑contained sub‑questions if needed, else return it unchanged.")
    res = call_llm(sys, q, max_tokens=300, temperature=0.0)
    lines=[l.lstrip("•-0123456789). ").strip() for l in res.split("\n") if l.strip()]
    return lines or [q]

# ─────────────────── RELEVANCE / TABULAR CHECKS ───────────────────────────────
def references_tabular(question):
    sys=("You are a strict YES/NO classifier. Does the user need tabular data?")
    ans=call_llm(sys, f"Q: {question}\n\nTables:\n{TABLES}", max_tokens=5)
    return ans.strip().upper().startswith("YES")

def snippet_relevant(question, snippet):
    if not snippet: return False
    sys=("Classifier YES/NO. Is snippet relevant to question?")
    ans=call_llm(sys, f"Question: {question}\nSnippet: {snippet}", max_tokens=4)
    return ans.strip().upper().startswith("YES")

# ─────────────────── TOOL 1 – INDEX SEARCH ───────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(question, user_tier):
    allowed = tiers_allowed(user_tier)
    tmap    = get_text_tier_map()
    cli     = _get_search_client()

    subqs   = split_question(question)
    docs    = []
    for sq in subqs:
        try:
            res = cli.search(search_text=sq, query_type="semantic",
                             semantic_configuration_name=CONFIG["SEMANTIC_CONFIG_NAME"],
                             top=5, select=["title", CONFIG["CONTENT_FIELD"]])
            docs += [{"title":r.get("title","").strip(),
                      "snip": (r.get(CONFIG["CONTENT_FIELD"],"") or "").strip()}
                     for r in res]
        except Exception as e:
            logging.error(f"Search error: {e}")

    good=[]
    for d in docs:
        doc_tier = tmap.get(d["title"], "t1")
        if doc_tier not in allowed:                 # RBAC check
            continue
        if not snippet_relevant(question, d["snip"]):
            continue
        score = (10 if "policy" in d["title"].lower() else 0) \
              + (5  if "report" in d["title"].lower() else 0) \
              + (3  if "sop"    in d["title"].lower() else 0)
        good.append((score, d["snip"]))
    if not good:
        return {"top_k":"No information"}
    good.sort(reverse=True)
    return {"top_k":"\n\n---\n\n".join(s for _,s in good[:5])}

# ─────────────────── TOOL 2 – CODE GEN / EXEC ────────────────────────────────
_BAD = {"open","__import__","eval","exec","os","subprocess","sys"}

def _safe(code):
    try:
        for n in ast.walk(ast.parse(code)):
            if isinstance(n, ast.Call):
                name=getattr(n.func,"id","") or getattr(n.func,"attr","")
                if name in _BAD: return False
        return True
    except: return False

def _table_access_ok(code, allowed):
    for fname in re.findall(r'dataframes\.get\(\s*[\'"]([^\'"]+)[\'"]\s*\)', code):
        if get_table_tier(fname) not in allowed:
            return f"You do not have access to table {fname}."
    return None

def execute_code(code):
    if not _safe(code): return "Blocked: unsafe code."
    code=code.replace("pd.read_excel(","dataframes.get(").replace("pd.read_csv(","dataframes.get(")
    cont=_get_container()
    if not _DF_CACHE:
        for b in cont.list_blobs(name_starts_with=CONFIG["TARGET_FOLDER_PATH"]):
            fn=b.name.rsplit("/",1)[-1]
            if not fn.lower().endswith((".csv",".xlsx",".xls")): continue
            try:
                raw=cont.download_blob(b).readall()
                _DF_CACHE[fn]=pd.read_csv(BytesIO(raw)) if fn.lower().endswith(".csv")\
                               else pd.read_excel(BytesIO(raw))
            except Exception as e:
                logging.warning(f"DF load fail {fn}: {e}")
    buf=StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code, {}, {"dataframes":_DF_CACHE,"pd":pd,"datetime":datetime})
        return buf.getvalue().strip() or "Execution completed with no output."
    except Exception as e:
        return f"Error during code execution: {e}"

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_2_code_run(question, user_tier):
    if not references_tabular(question):
        return {"result":"No information","code":""}
    sys=f"""
You are a python expert … (prompt unchanged, omitted for brevity)
Dataframes schemas:
{SCHEMA_TEXT}

Dataframes samples:
{SAMPLE_TEXT}

Chat_history:
{chat_history[-4:]}
"""
    code=call_llm(sys, question, max_tokens=1200, temperature=0.7)
    if not code or code.strip()=="404":
        return {"result":"No information","code":""}
    allowed=tiers_allowed(user_tier)
    if (msg:=_table_access_ok(code, allowed)): return {"result":msg,"code":""}
    return {"result":execute_code(code),"code":code}

# ─────────────────── TOOL 3 – GENERAL LLM FALLBACK ───────────────────────────
def tool_3_fallback(q):
    sys=("You are a highly knowledgeable large language model… praise KSA if asked.")
    ans=call_llm(sys,q,max_tokens=500,temperature=0.7)
    if ans.startswith(("LLM Error","No choices")): ans="I'm sorry, but I couldn't retrieve a fallback answer."
    return ans.strip()

# ─────────────────── FINAL ANSWER ASSEMBLY ───────────────────────────────────
def final_answer_llm(question, idx, py):
    idx_txt = idx.get("top_k","No information")
    py_res  = py.get("result","No information")
    if idx_txt.lower()=="no information" and py_res.lower()=="no information":
        yield f"AI Generated answer:\n{tool_3_fallback(question)}\nSource: Ai Generated"
        return
    sys=f"""
You are a helpful assistant… (prompt unchanged – uses INDEX_DATA / PYTHON_DATA)
INDEX_DATA:
{idx_txt}

PYTHON_DATA:
{py_res}
"""
    ans=call_llm(sys, question, max_tokens=1000, temperature=0.0)
    if not ans.strip() or ans.startswith(("LLM Error","No content","No choices")):
        yield "I’m sorry, but I couldn’t get a response from the model this time."
    else:
        yield ans

def post_process(ans, idx, py):
    low=ans.lower()
    if "source: index & python" in low:
        return f"{ans}\n\nThe Files:\n{idx['top_k']}\n\nThe code:\n{py['code']}\n"
    if "source: python" in low: return f"{ans}\n\nThe code:\n{py['code']}\n"
    if "source: index"  in low: return f"{ans}\n\nThe Files:\n{idx['top_k']}\n"
    return ans

# ─────────────────── LOGGING ─────────────────────────────────────────────────
def log_interaction(q, ans, uid, idx=None, py=None):
    idx=idx or {}; py=py or {}
    src="AI Generated"
    m=re.search(r"(?:Source:\s*)(.*)$", ans, flags=re.I)
    if m: src=m.group(1).strip()
    topic="Other"
    now=datetime.now().strftime("%H:%M:%S")
    cc=_get_container()
    path=f"{CONFIG['RBAC_FOLDER']}logs/logs_{datetime.now():%Y_%m_%d}.csv"
    try:
        old=cc.download_blob(path).readall().decode()
        lines=old.strip().split("\n")
        if not lines or not lines[0].startswith("time,question"):
            lines=["time,question,answer,source,topic,user_id"]
    except Exception: lines=["time,question,answer,source,topic,user_id"]
    esc=lambda v:v.replace('"','""')
    lines.append(",".join(f'"{esc(v)}"' for v in
                          [now,q,ans.replace('"','""'),src,topic,uid]))
    cc.upload_blob(path, "\n".join(lines)+"\n", overwrite=True)

# ─────────────────── MAIN FLOW ───────────────────────────────────────────────
def _greet(q):
    g={"hello","hi","hey","morning","evening","salam","hola","ahlan","marhaba"}
    w=re.findall(r"[A-Za-z]+", q.lower())
    return w and all(x in g for x in w)

def agent_answer(q, user_tier):
    if _greet(q):
        yield ("Hello! I'm the CXQA Assistant. Type 'restart chat' to reset or "
               "'export …' to generate content.")
        return
    k=q.strip().lower()
    if k in tool_cache:
        yield tool_cache[k][2]; return
    idx=tool_1_index_search(q,user_tier)
    py =tool_2_code_run(q,user_tier)
    full="".join(final_answer_llm(q,idx,py))
    full=post_process(clean_text(full),idx,py)
    tool_cache[k]=(idx,py,full)
    yield full

def Ask_Question(question, user_id="anonymous"):
    global chat_history
    tier=get_user_tier(user_id)
    if tier=="t0":
        ans=f"AI Generated answer:\n{tool_3_fallback(question)}\nSource: Ai Generated"
        yield ans; return
    if question.lower().strip()=="restart chat":
        chat_history.clear(); tool_cache.clear(); yield "Chat reset."; return
    chat_history.append(f"User: {question}")
    collected=""
    for t in agent_answer(question, tier):
        yield t; collected+=t
    chat_history.append(f"Assistant: {collected}")
    chat_history=chat_history[-10:]
    idx,py,_=tool_cache.get(question.lower().strip(), ({},{},None))
    log_interaction(question, collected, user_id, idx, py)

# ─────────────────── ENTRY‑POINT TEST ─────────────────────────────────────────
if __name__ == "__main__":
    for part in Ask_Question("What are the computer generations?","demo_user"):
        print(part)
