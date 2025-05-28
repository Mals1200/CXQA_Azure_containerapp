
questions = [
    "what to do if there was a fire?",
    "what to do if there was a lost child?"
]
user_id = "Malsabhan@diriyah.sa"  # or use your actual Teams user ID if RBAC is needed

# Determine user tier (so you get the same results as the bot)
user_tier = get_user_tier(user_id)

for q in questions:
    print("="*70)
    print(f"QUESTION: {q}\n")
    print(f"User tier: {user_tier}")
    # Directly run index search, don't use full agent/LLM answer
    index_result = tool_1_index_search(q, top_k=5, user_tier=user_tier, question_primarily_tabular=False)
    file_names = index_result.get("file_names", [])
    top_k_text = index_result.get("top_k", "")

    print(f"\nFiles referenced: {file_names}")
    print("\nTop 5 Chunks from Index:\n")
    # Split top_k_text by --- if possible (that's how they're joined)
    chunks = top_k_text.split("\n\n---\n\n")
    for i, chunk in enumerate(chunks[:5]):
        print(f"--- Chunk {i+1} ---\n{chunk.strip()}\n")
    print("="*70 + "\n")
