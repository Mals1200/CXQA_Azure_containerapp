import urllib.parse

SHAREPOINT_BASE = (
    "https://dgda.sharepoint.com/:x:/r/"
    "sites/CXQAData/_layouts/15/Doc.aspx?"
    "sourcedoc=%7B9B3CA3CD-5044-45C7-8A82-0604A1675F46%7D"
    "&file={}&action=default&mobileredirect=true"
)

def link(fname: str) -> str:
    return SHAREPOINT_BASE.format(urllib.parse.quote(fname))

def render(question: str, content: list, source: str, files: list, tables: list) -> str:
    lines = []

    # Q1 prefix
    lines.append(f"🗨️ Q1: {question.strip()}")
    lines.append(question.strip())
    lines.append("")

    # Content
    for item in content:
        t   = item.get("type")
        txt = item.get("text", "")
        if t == "heading":
            lines.append(f"**{txt}**")
            lines.append("")
        elif t == "paragraph" and not txt.startswith(("Referenced:", "Calculated")):
            lines.append(txt)
            lines.append("")
        elif t == "numbered_list":
            for i, li in enumerate(item.get("items", []), 1):
                lines.append(f"{i}. {li}")
            lines.append("")
        elif t == "bullet_list":
            for li in item.get("items", []):
                lines.append(f"• {li}")
            lines.append("")

    # Source
    lines.append(f"Source: {source}")
    lines.append("")

    # Referenced
    if files:
        lines.append("Referenced:")
        for f in files:
            lines.append(f"- [{f}]({link(f)})")
        lines.append("")

    # Calculated using
    if tables:
        lines.append("Calculated using:")
        for t in tables:
            lines.append(f"- [{t}]({link(t)})")

    return "\n".join(lines)
