# Version (1)
# Uses the "cxqa-ind-v6", which was created using azure ai hub
# the semantic is a default option. the information is stored in the meta data chunk.

def tool_1_index_search(user_question, top_k=5):
    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    INDEX_NAME = "cxqa-ind-v6" # vector-1741789014893 / cxqa-ind-v6 / vector-1741790186391-12-3-2025
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    subquestions = split_question_into_subquestions(user_question)

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        results = search_client.search(
            search_text=user_question,
            query_type="semantic",
            semantic_configuration_name="azureml-default",
            top=top_k,
            include_total_count=False
        )

        relevant_texts = []
        for r in results:
            snippet = r.get("content", "").strip()
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

    except Exception as e:
        logging.error(f"Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}



# Version (2) choosing different Indexes with other content metadata.
# Can switch b/t  the "cxqa-ind-v6" and the "vector-1741865904949", which was created using azure ai hub
# the semantic is a default option for the "cxqa-ind-v6" or a tailored one for "vector-1741865904949". 
# the information is stored in the meta data "content" or "chunk".

def tool_1_index_search(user_question, top_k=5):
    """
    Searches the Azure AI Search index using semantic search and retrieves top_k results.
    This function allows switching between `cxqa-ind-v6` (old) and `vector-1741790186391-12-3-2025` (new)
    by **changing the index name, semantic configuration, and content field**.
    
    Parameters:
        - user_question (str): The query to search.
        - top_k (int): Number of top results to retrieve.

    Returns:
        - dict: A dictionary with the search results.
    """

    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    # 🔹 CHOOSE INDEX (Comment/Uncomment as needed)
    INDEX_NAME = "vector-1741865904949"  # ✅ Use new index
    # INDEX_NAME = "cxqa-ind-v6"  # ✅ Use old index

    # 🔹 CHOOSE SEMANTIC CONFIGURATION (Comment/Uncomment as needed)
    SEMANTIC_CONFIG_NAME = "vector-1741865904949-semantic-configuration"  # ✅ Use for new index
    # SEMANTIC_CONFIG_NAME = "azureml-default"  # ✅ Use for old index

    # 🔹 CHOOSE CONTENT FIELD (Comment/Uncomment as needed)
    CONTENT_FIELD = "chunk"  # ✅ Use for new index
    # CONTENT_FIELD = "content"  # ✅ Use for old index

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_KEY)
        )

        # 🔹 Perform the search with explicit field selection
        logging.info(f"🔍 Searching in Index: {INDEX_NAME}")
        results = search_client.search(
            search_text=user_question,
            query_type="semantic",
            semantic_configuration_name=SEMANTIC_CONFIG_NAME,
            top=top_k,
            select=["title", CONTENT_FIELD],  # ✅ Ensure the correct content field is retrieved
            include_total_count=False
        )

        relevant_texts = []
        for r in results:
            snippet = r.get(CONTENT_FIELD, "").strip()
            if snippet:  # Avoid empty results
                relevant_texts.append(snippet)

        if not relevant_texts:
            return {"top_k": "No information"}

        combined = "\n\n---\n\n".join(relevant_texts)
        return {"top_k": combined}

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}



# Version (3) File ranking:
# The same as version (2) just added the file weights based on present words in the title.
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def tool_1_index_search(user_question, top_k=5):
    """
    Searches the Azure AI Search index using semantic search and retrieves top_k results.
    This function allows switching between `cxqa-ind-v6` (old) and `vector-1741790186391-12-3-2025` (new)
    by **changing the index name, semantic configuration, and content field**.
    
    Parameters:
        - user_question (str): The query to search.
        - top_k (int): Number of top results to retrieve.

    Returns:
        - dict: A dictionary with the search results.
    """

    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    # 🔹 CHOOSE INDEX (Comment/Uncomment as needed)
    INDEX_NAME = "vector-1741865904949"  # ✅ Use new index
    # INDEX_NAME = "cxqa-ind-v6"  # ✅ Use old index

    # 🔹 CHOOSE SEMANTIC CONFIGURATION (Comment/Uncomment as needed)
    SEMANTIC_CONFIG_NAME = "vector-1741865904949-semantic-configuration"  # ✅ Use for new index
    # SEMANTIC_CONFIG_NAME = "azureml-default"  # ✅ Use for old index

    # 🔹 CHOOSE CONTENT FIELD (Comment/Uncomment as needed)
    CONTENT_FIELD = "chunk"  # ✅ Use for new index
    # CONTENT_FIELD = "content"  # ✅ Use for old index

    try:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        # 🔹 Perform the search with explicit field selection
        logging.info(f"🔍 Searching in Index: {INDEX_NAME}")
        results = search_client.search(
            search_text=user_question,
            query_type="semantic",
            semantic_configuration_name=SEMANTIC_CONFIG_NAME,
            top=top_k,
            select=["title", CONTENT_FIELD],  # ✅ Ensure the correct content field is retrieved
            include_total_count=False
        )

        # Keep original logic of collecting snippets:
        relevant_texts = []
        # Collect docs so we can do weighting:
        docs = []

        for r in results:
            snippet = r.get(CONTENT_FIELD, "").strip()
            title = r.get("title", "").strip()
            if snippet:  # Avoid empty results
                relevant_texts.append(snippet)
                docs.append({"title": title, "snippet": snippet})

        if not relevant_texts:
            return {"top_k": "No information"}

        # 🔹 Apply weighting based on keywords in title (case-insensitive)
        for doc in docs:
            ttl = doc["title"].lower()
            score = 0
            if "policy" in ttl:
                score += 10
            if "report" in ttl:
                score += 5
            if "sop" in ttl:
                score += 3
            doc["weight_score"] = score

        # 🔹 Sort docs by descending weight
        docs_sorted = sorted(docs, key=lambda x: x["weight_score"], reverse=True)

        # 🔹 Slice top_k after re-ranking
        docs_top_k = docs_sorted[:top_k]

        # Prepare final combined text as before:
        re_ranked_texts = [d["snippet"] for d in docs_top_k]
        combined = "\n\n---\n\n".join(re_ranked_texts)

        return {"top_k": combined}

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}




# Version (4) : incorporate the split_question_into_subquestions function:
# The same as version (2) just corrected an error that made this tool see and incorporate split_question_into_subquestions.
def tool_1_index_search(user_question, top_k=5):
    """
    Modified version: uses split_question_into_subquestions to handle multi-part queries.
    Searches each subquestion individually, merges the results, then re-ranks.
    """

    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    INDEX_NAME = "vector-1741865904949"  
    SEMANTIC_CONFIG_NAME = "vector-1741865904949-semantic-configuration"  
    CONTENT_FIELD = "chunk"

    # ---------------------
    # 1) Split into subquestions
    # ---------------------
    subquestions = split_question_into_subquestions(user_question, use_semantic_parsing=True)
    # If for some reason the list is empty or we can't parse it, fall back to [user_question]
    if not subquestions:
        subquestions = [user_question]

    try:
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential

        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        # We'll gather docs from all subquestions in a single list:
        merged_docs = []

        # ---------------------
        # 2) For each subquestion, do the same search
        # ---------------------
        for subq in subquestions:
            logging.info(f"🔍 Searching in Index for subquestion: {subq}")
            results = search_client.search(
                search_text=subq,
                query_type="semantic",
                semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                top=top_k,
                select=["title", CONTENT_FIELD],
                include_total_count=False
            )

            # Convert results to a list of {title, snippet}
            for r in results:
                snippet = r.get(CONTENT_FIELD, "").strip()
                title = r.get("title", "").strip()
                if snippet:
                    merged_docs.append({"title": title, "snippet": snippet})

        # If we got no documents across all subquestions:
        if not merged_docs:
            return {"top_k": "No information"}

        # ---------------------
        # 3) Relevance filtering
        # ---------------------
        relevant_docs = []
        for doc in merged_docs:
            snippet = doc["snippet"]
            if is_text_relevant(user_question, snippet):
                relevant_docs.append(doc)

        if not relevant_docs:
            return {"top_k": "No information"}

        # ---------------------
        # 4) Apply weighting for certain keywords in the title
        # ---------------------
        for doc in relevant_docs:
            ttl = doc["title"].lower()
            score = 0
            if "policy" in ttl:
                score += 10
            if "report" in ttl:
                score += 5
            if "sop" in ttl:
                score += 3
            doc["weight_score"] = score

        # ---------------------
        # 5) Sort by weight_score descending
        # ---------------------
        docs_sorted = sorted(relevant_docs, key=lambda x: x["weight_score"], reverse=True)

        # ---------------------
        # 6) Slice top_k from merged results
        # ---------------------
        docs_top_k = docs_sorted[:top_k]

        # Prepare final combined text
        re_ranked_texts = [d["snippet"] for d in docs_top_k]
        combined = "\n\n---\n\n".join(re_ranked_texts)

        return {"top_k": combined}

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}

# Version (6) : make it retrieve 12 top_k, then short list it to 5.
def tool_1_index_search(user_question, top_k=5):
    """
    Modified version: uses split_question_into_subquestions to handle multi-part queries.
    Internally always fetches 12 results per subquestion (FETCH_LIMIT), 
    then re-ranks and slices to 'top_k' in the final step.
    The function signature remains the same (top_k=5 by default).
    """

    SEARCH_SERVICE_NAME = "cxqa-azureai-search"
    SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE_NAME}.search.windows.net"
    ADMIN_API_KEY = "COsLVxYSG0Az9eZafD03MQe7igbjamGEzIElhCun2jAzSeB9KDVv"

    INDEX_NAME = "vector-1741865904949"
    SEMANTIC_CONFIG_NAME = "vector-1741865904949-semantic-configuration"
    CONTENT_FIELD = "chunk"

    FETCH_LIMIT = 12  # We will always retrieve 12 initially

    # 1) Split into subquestions
    subquestions = split_question_into_subquestions(user_question, use_semantic_parsing=True)
    if not subquestions:
        subquestions = [user_question]

    try:
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential

        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_API_KEY)
        )

        merged_docs = []

        # 2) For each subquestion, fetch 12 results
        for subq in subquestions:
            logging.info(f"🔍 Searching in Index for subquestion: {subq}")
            results = search_client.search(
                search_text=subq,
                query_type="semantic",
                semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                top=FETCH_LIMIT,  # always retrieve 12
                select=["title", CONTENT_FIELD],
                include_total_count=False
            )
            for r in results:
                snippet = r.get(CONTENT_FIELD, "").strip()
                title = r.get("title", "").strip()
                if snippet:
                    merged_docs.append({"title": title, "snippet": snippet})

        if not merged_docs:
            return {"top_k": "No information"}

        # 3) Relevance filtering
        relevant_docs = []
        for doc in merged_docs:
            snippet = doc["snippet"]
            if is_text_relevant(user_question, snippet):
                relevant_docs.append(doc)

        if not relevant_docs:
            return {"top_k": "No information"}

        # 4) Apply weighting for certain keywords in the title
        for doc in relevant_docs:
            ttl = doc["title"].lower()
            score = 0
            if "policy" in ttl:
                score += 10
            if "report" in ttl:
                score += 5
            if "sop" in ttl:
                score += 3
            doc["weight_score"] = score

        # 5) Sort by weight_score descending
        docs_sorted = sorted(relevant_docs, key=lambda x: x["weight_score"], reverse=True)

        # 6) Finally, slice 'top_k' from that sorted list
        docs_top_k = docs_sorted[:top_k]

        # Prepare final combined text
        re_ranked_texts = [d["snippet"] for d in docs_top_k]
        combined = "\n\n---\n\n".join(re_ranked_texts)

        return {"top_k": combined}

    except Exception as e:
        logging.error(f"⚠️ Error in Tool1 (Index Search): {str(e)}")
        return {"top_k": "No information"}
