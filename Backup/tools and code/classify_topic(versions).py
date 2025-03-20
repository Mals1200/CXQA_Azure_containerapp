#######################################
# Version(1) Broken
# throwing a 400 error
#######################################




#######################################
# Version(2) Fix
# fixed throwing a 400 error
#######################################

def classify_topic(question, answer, recent_history):
    """
    Classify the conversation into exactly one category from:
      [Policy, SOP, Report, Analysis, Exporting_file, Other]
    based on:
      - question
      - answer
      - recent_history (list of up to 4 recent messages)

    Returns: A single string from the above list of topics.
    """

    import requests
    import json

    # 1) Use the same endpoint & version that works in your environment
    LLM_ENDPOINT = (
        "https://cxqaazureaihub2358016269.openai.azure.com/"
        "openai/deployments/gpt-4o-3/chat/completions"
        "?api-version=2024-08-01-preview"
    )
    # 2) Your real Azure OpenAI key here
    LLM_API_KEY = "Cv54PDKaIusK0dXkMvkBbSCgH982p1CjUwaTeKlir1NmB6tycSKMJQQJ99AKACYeBjFXJ3w3AAAAACOGllor"

    # 3) Construct minimal system+user prompts
    system_prompt = (
        "You are a classification model. Based on the question, the last 4 messages of history, "
        "and the final answer, classify the conversation into exactly one category: "
        "[Policy, SOP, Report, Analysis, Exporting_file, Other]. "
        "Respond ONLY with that single category name, no extra words."
    )

    user_prompt = (
        f"Question: {question}\n"
        f"Recent History: {recent_history}\n"
        f"Final Answer: {answer}\n"
        f"Return only one topic from [Policy, SOP, Report, Analysis, Exporting_file, Other]."
    )

    # 4) Prepare the payload
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 20,
        "temperature": 0.0
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY
    }

    # 5) Make the request
    try:
        response = requests.post(LLM_ENDPOINT, headers=headers, json=payload, timeout=20)

        if not response.ok:
            # Print or log the error info for debugging
            print("classify_topic Error:", response.status_code, response.text)
            response.raise_for_status()

        data = response.json()
        # The classification is presumably in choices[0].message.content
        classification = data["choices"][0]["message"].get("content", "").strip()

        allowed_topics = ["Policy", "SOP", "Report", "Analysis", "Exporting_file", "Other"]
        return classification if classification in allowed_topics else "Other"

    except Exception as e:
        print("Exception in classify_topic:", str(e))
        # Fallback topic if something goes wrong
        return "Other"
