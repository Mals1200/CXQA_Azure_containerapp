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



# Version (2)
# Can switch b/t  the "cxqa-ind-v6" and the "vector-1741790186391-12-3-2025", which was created using azure ai hub
# the semantic is a default option for the "cxqa-ind-v6" or a tailored one for "vector-1741790186391-12-3-2025". 
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
    # INDEX_NAME = "vector-1741790186391-12-3-2025"  # ✅ Use new index
    INDEX_NAME = "cxqa-ind-v6"  # ✅ Use old index

    # 🔹 CHOOSE SEMANTIC CONFIGURATION (Comment/Uncomment as needed)
    # SEMANTIC_CONFIG_NAME = "vector-1741790186391-12-3-2025-semantic-configuration"  # ✅ Use for new index
    SEMANTIC_CONFIG_NAME = "azureml-default"  # ✅ Use for old index

    # 🔹 CHOOSE CONTENT FIELD (Comment/Uncomment as needed)
    # CONTENT_FIELD = "chunk"  # ✅ Use for new index
    CONTENT_FIELD = "content"  # ✅ Use for old index

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
