import urllib.parse

SHAREPOINT_BASE = (
    "https://dgda.sharepoint.com/:x:/r/"
    "sites/CXQAData/_layouts/15/Doc.aspx?"
    "sourcedoc=%7B9B3CA3CD-5044-45C7-8A82-0604A1675F46%7D"
    "&file={}&action=default&mobileredirect=true"
)

def link(fname: str) -> str:
    url = SHAREPOINT_BASE.format(urllib.parse.quote(fname))
    return f"[{fname}]({url})"

def render(question: str, content: list, source: str, files: list, tables: list) -> str:
    lines = []
    # Q1 prefix
    lines.append(f"🗨️ Q1: {question.strip()}")
    lines.append(question.strip())
    lines.append("")  # blank line

    # Process headings and paragraphs/numbered lists
    for item in content:
        t = item.get("type")
        txt = item.get("text", "")
        if t == "heading":
            lines.append(f"**{txt}**")
            lines.append("")  # blank after heading
        elif t == "paragraph" and not txt.startswith(("Referenced:", "Calculated")):
            lines.append(txt)
            lines.append("")
        elif t == "numbered_list":
            for idx, it in enumerate(item.get("items", []), 1):
                lines.append(f"{idx}. {it}")
            lines.append("")

    # Source line
    lines.append(f"Source: {source}")

    # File hyperlinks
    for f in files:
        lines.append(f"- {link(f)}")
    # Tables as “calculated using”
    for t in tables:
        lines.append(f"- Calculated using: {link(t)}")

    return "\n".join(lines)
