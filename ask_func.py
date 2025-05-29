# ask_func.py - Minimal test version for Teams Adaptive Card debugging

def Ask_Question(question, user_id="anonymous"):
    """
    Always returns a valid, small JSON answer for any question.
    This lets you test Teams display with a simple card.
    """
    # Minimal JSON answer with simple content
    test_json_answer = {
        "content": [
            {
                "type": "heading",
                "text": f"Test Card for Teams"
            },
            {
                "type": "paragraph",
                "text": f"You asked: {question}"
            },
            {
                "type": "bullet_list",
                "items": [
                    "This is a test bullet",
                    "If you see this in Teams, your card plumbing is correct",
                    "Try your real ask_func.py again after this test"
                ]
            }
        ],
        "source": "AI Generated"
    }

    # Always return as a single JSON line
    import json
    yield json.dumps(test_json_answer)
