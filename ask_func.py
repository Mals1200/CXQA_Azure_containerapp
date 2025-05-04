# ─────────────────── ASK QUESTIONS & EXPORT ANSWERS WITH SOURCE ──────────────────────────
import time
import csv
import os
# ─────────────────── Wait before the start ──────────────────────────────────────────────────────────────
# start_wait = 60
# print(f"Starting...\n Waiting {start_wait} Seconds")
# time.sleep(start_wait)

# ─────────────────── HELPER ──────────────────────────────────────────────────────────────
def _parse_answer(full_answer: str):
    """
    Split the model’s full answer into:
      • clean answer  (everything before 'Source:')
      • source_type   (first line after 'Source:')
      • source_material (any remaining lines after that)
    """
    if "Source:" in full_answer:
        answer_part, src_part = full_answer.split("Source:", 1)
        answer_part = answer_part.strip()
        src_part    = src_part.strip()
        src_lines        = src_part.splitlines()
        source_type      = src_lines[0].strip()
        source_material  = "\n".join(src_lines[1:]).strip()
    else:
        # Fallback if no explicit source tag
        answer_part     = full_answer.strip()
        source_type     = "Unknown"
        source_material = ""
    return answer_part, source_type, source_material

# ─────────────────── MAIN ────────────────────────────────────────────────────────────────
def main():
    USER_EMAIL = "nramesh@diriyah.sa"

    QUESTIONS = [
        # "what is the visits in al bujairy on the 12th oct 2024?",
        # "What to do if there was a fire?",
        "what is the visits in al bujairy on the 12th oct 2024 and What to do if there was a fire?",
        # "What to do if there was a lost child and if there was a fire?"
    ]
    # CSV destination on Desktop
    desktop_path = r"C:\Users\malsabhan\OneDrive - Diriyah Gate Company Limited\Desktop"
    csv_file     = os.path.join(desktop_path, "questions_and_answers.csv")
    Wait_time = 120
    with open(csv_file, mode="w", newline="", encoding="utf-8") as fout:
        writer = csv.writer(fout)
        writer.writerow(["question", "answer", "source_type", "source_material"])

        for idx, question in enumerate(QUESTIONS, start=1):
            print(f"\n🗨️  Q{idx}: {question}")
            try:
                # Ask the question and stream tokens to console
                answer_tokens = Ask_Question(question, USER_EMAIL)
                collected     = []
                for tok in answer_tokens:
                    print(tok, end='', flush=True)
                    collected.append(tok)
                full_answer = "".join(collected)
                # Parse and record
                answer, src_type, src_material = _parse_answer(full_answer)
            except Exception as err:
                print(f"\n❌ Error on question {idx}: {err}")
                answer, src_type, src_material = "", "Error", str(err)
            writer.writerow([question, answer, src_type, src_material])
            print("\n")  # neat spacing in console
            # Wait 60 s between questions (respect rate limits, etc.)
            sep = 80
            if idx < len(QUESTIONS):
                print("⏳ Waiting ", Wait_time, " seconds …\n", ("=" * sep + "=\n")*10)
                time.sleep(Wait_time)

print("(Staring)\n")                
if __name__ == "__main__":
    main()
