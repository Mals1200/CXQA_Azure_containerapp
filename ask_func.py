# ─────────────────── ASK QUESTIONS & EXPORT ANSWERS WITH SOURCE ──────────────────────────
"""
Runs a list of questions against Ask_Question(question, user_email) and
records the response into a CSV located on the Desktop with columns:
    • question
    • answer        (content only, without internal Calculated/Referenced blocks)
    • source_type   (Python / Index / Index & Python / Unknown)
    • Files used    (comma‑separated file &/or table names)

Console output now shows:
    1) the cleaned answer content
    2) "Source: <source_type>"
    3) the files used (one line, comma‑separated) or “(no files)”

This eliminates the awkward ordering where the source type appeared between
file names. All other behaviour (waits, restart‑chat handling) remains the same.
"""

import os
import csv
import json
import time
from typing import List, Tuple

# ─────────────────── Wait before the start ──────────────────────────────────────────────────────────────
# start_wait = 420
# print(f"Starting...\n Waiting {start_wait} Seconds")
# time.sleep(start_wait)
# ─────────────────── CONFIG ────────────────────────────────────────────────────────────
USER_EMAIL  = "nramesh@diriyah.sa"
WAIT_TIME_S = 20  # seconds between API calls
OUTPUT_DIR  = os.path.join(
    os.path.expanduser("~"),
    r"OneDrive - Diriyah Gate Company Limited",
    "Desktop/compare",
)
CSV_PATH    = os.path.join(OUTPUT_DIR, "sharing_test/new_Qs_Opt.csv")




# ─────────────────── HELPERS ───────────────────────────────────────────────────────────

def _render_content(blocks: List[dict]) -> str:
    """Render structured LLM content → plain text while **omitting** any internal
    Calculated/Referenced sections (those will be printed separately)."""
    out: List[str] = []
    skip_mode = False  # True while inside Calc/Ref bullets we plan to skip

    for blk in blocks:
        btype = blk.get("type", "")
        txt   = blk.get("text", "")

        # Detect and skip "Calculated using:" or "Referenced:" paragraphs + their bullets
        if btype == "paragraph" and txt.lower().startswith(("calculated using", "referenced")):
            skip_mode = True
            continue  # skip marker line
        if skip_mode and btype in ("paragraph", "bullet_list", "numbered_list"):
            # Still skipping until a new heading arrives
            if btype == "heading":
                skip_mode = False  # end skip on new section
            else:
                continue

        if skip_mode:
            continue

        if btype == "heading":
            out.append(txt.strip())
            out.append("")
        elif btype == "paragraph":
            out.append(txt.strip())
            out.append("")
        elif btype == "bullet_list":
            out.extend(f"• {item}" for item in blk.get("items", []))
            out.append("")
        elif btype == "numbered_list":
            out.extend(f"{i}. {item}" for i, item in enumerate(blk.get("items", []), 1))
            out.append("")
        else:  # unknown – stringify
            out.append(str(blk))
            out.append("")

    return "\n".join(out).strip()


def _parse_answer(full: str) -> Tuple[str, str, str]:
    """Return (clean_answer, source_type, files_used)."""
    # 1) Prefer JSON schema
    try:
        js = json.loads(full)
        answer      = _render_content(js.get("content", [])) or full
        source_type = js.get("source", "Unknown")
        det         = js.get("source_details", {})
        files       = det.get("file_names", []) + det.get("table_names", [])
        files_used  = ", ".join(files)
        return answer, source_type, files_used
    except (json.JSONDecodeError, TypeError):
        pass

    # 2) Legacy plain‑text splitter
    if "Source:" in full:
        ans, src_part = full.split("Source:", 1)
        ans_clean = ans.strip()
        src_lines = [l.strip() for l in src_part.splitlines() if l.strip()]
        src_type  = src_lines[0] if src_lines else "Unknown"
        files     = ", ".join(src_lines[1:]) if len(src_lines) > 1 else ""
        return ans_clean, src_type, files

    return full.strip(), "Unknown", ""


# ─────────────────── MAIN ───────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("(Starting)\n")

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as csv_f:
        writer = csv.writer(csv_f)
        writer.writerow(["question", "answer", "source_type", "Files used"])

        for idx, q in enumerate(QUESTIONS, 1):
            print(f"🗨️  Q{idx}: {q}")
            try:
                toks = Ask_Question(q, USER_EMAIL)  # function defined elsewhere
                full_answer = "".join(toks)
                answer, src, files = _parse_answer(full_answer)
            except Exception as e:
                answer, src, files = "", "Error", str(e)
                print(f"❌ {e}")

            # ── console ──
            print(answer)
            print(f"Source: {src}")
            print(files or "(no files)")
            print("\n" + "=" * 80 + "\n" + "\n" + "=" * 80 + "\n")

            # ── CSV ──
            writer.writerow([q, answer, src, files])
            csv_f.flush()

            # wait unless last
            if idx < len(QUESTIONS):
                print(f"⏳ Waiting {WAIT_TIME_S} seconds …\n")
                time.sleep(WAIT_TIME_S)


if __name__ == "__main__":
    main()
