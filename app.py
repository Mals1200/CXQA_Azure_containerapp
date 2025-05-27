# version 11b 
# ((Hyperlink file names))
# Made it display the files sources for the compounded questions:
    # Referenced: <Files>                <-------Hyperlink to sharepoint
    # Calculated using: <Tables>         <-------Hyperlink to sharepoint
# still the url is fixed to one file. (NEEDS WORK!)

import os
import asyncio
from threading import Lock
import re
import json
import urllib.parse

from flask import Flask, request, jsonify, Response
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext
)
from botbuilder.schema import Activity
# *** Important: import TeamsInfo ***
from botbuilder.core.teams import TeamsInfo

from ask_func import Ask_Question, chat_history

app = Flask(__name__)

MICROSOFT_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Thread-safe conversation state management
conversation_states = {}
state_lock = Lock()

def get_conversation_state(conversation_id):
    with state_lock:
        if conversation_id not in conversation_states:
            conversation_states[conversation_id] = {
                'history': [],
                'cache': {},
                'last_activity': None
            }
        return conversation_states[conversation_id]

def cleanup_old_states():
    """Clean up conversation states older than 24 hours"""
    with state_lock:
        current_time = asyncio.get_event_loop().time()
        for conv_id, state in list(conversation_states.items()):
            if state['last_activity'] and (current_time - state['last_activity']) > 86400:  # 24 hours
                del conversation_states[conv_id]

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "API is running!"}), 200

@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    body = request.json
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(adapter.process_activity(activity, auth_header, _bot_logic))
    finally:
        loop.close()

    return Response(status=200)

async def _bot_logic(turn_context: TurnContext):
    conversation_id = turn_context.activity.conversation.id
    state = get_conversation_state(conversation_id)
    state['last_activity'] = asyncio.get_event_loop().time()
    
    # Clean up old states periodically
    if len(conversation_states) > 100:  # Only clean up if we have many states
        cleanup_old_states()

    # Set the conversation state for this request
    import ask_func
    ask_func.chat_history = state['history']
    ask_func.tool_cache = state['cache']

    user_message = turn_context.activity.text or ""

    # --------------------------------------------------------------------
    # Use 'TeamsInfo.get_member' to get userPrincipalName or email
    # --------------------------------------------------------------------
    user_id = "anonymous"  # fallback
    try:
        # 'from_property.id' usually holds the "29:..." Teams user ID
        teams_user_id = turn_context.activity.from_property.id

        # This call will attempt to fetch the user's profile from Teams
        teams_member = await TeamsInfo.get_member(turn_context, teams_user_id)
        # If successful, you can read these fields:
        #   teams_member.user_principal_name (often the email/UPN)
        #   teams_member.email
        #   teams_member.name
        #   teams_member.id
        if teams_member and teams_member.user_principal_name:
            user_id = teams_member.user_principal_name
        elif teams_member and teams_member.email:
            user_id = teams_member.email
        else:
            user_id = teams_user_id  # fallback if we can't get an email

    except Exception as e:
        # If get_member call fails (e.g., in a group chat scenario or permission issues),
        # just fallback to the "29:..." ID or 'anonymous'
        user_id = turn_context.activity.from_property.id or "anonymous"

    # Show "thinking" indicator
    typing_activity = Activity(type="typing")
    await turn_context.send_activity(typing_activity)

    try:
        # Process the message
        ans_gen = Ask_Question(user_message, user_id=user_id)
        answer_text = "".join(ans_gen)

        # Update state
        state['history'] = ask_func.chat_history
        state['cache'] = ask_func.tool_cache

        # Parse and format the response
        source_pattern = r"(.*?)\s*(Source:.*?)(---SOURCE_DETAILS---.*)?$"
        match = re.search(source_pattern, answer_text, flags=re.DOTALL)

        # Try to parse the response as JSON first
        try:
            # Remove code block markers if present
            cleaned_answer_text = answer_text.strip()
            if cleaned_answer_text.startswith('```json'):
                cleaned_answer_text = cleaned_answer_text[7:].strip()
            if cleaned_answer_text.startswith('```'):
                cleaned_answer_text = cleaned_answer_text[3:].strip()
            if cleaned_answer_text.endswith('```'):
                cleaned_answer_text = cleaned_answer_text[:-3].strip()
            # NOTE: Do NOT blindly escape real newline characters, as JSON allows
            # whitespace outside of string literals. Escaping them globally breaks
            # the JSON structure (e.g. producing `{\n` which is invalid). The
            # answers returned from `ask_func` are already serialized using
            # `json.dumps`, so any newline characters that _must_ be escaped are
            # already handled. Therefore, we simply attempt to load the JSON as-is.
            response_json = json.loads(cleaned_answer_text)
            # Check if this is our expected JSON format with content and source
            if isinstance(response_json, dict) and "content" in response_json and "source" in response_json:
                # We have a structured JSON response!
                content_items = response_json["content"]
                source = response_json["source"]
                # Build the adaptive card body
                body_blocks = []
                #referenced_paragraphs = []
                #calculated_paragraphs = []
                #other_paragraphs = []
                # Process each content item based on its type
                for item in content_items:
                    item_type = item.get("type", "")
                    if item_type == "heading":
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": item.get("text", ""),
                            "wrap": True,
                            "weight": "Bolder",
                            "size": "Large",
                            "spacing": "Medium"
                        })
                    elif item_type == "paragraph":
                        text = item.get("text", "")
                        # Only add to main body if not a reference/calculated paragraph
                        if not (text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:")):
                            body_blocks.append({
                                "type": "TextBlock",
                                "text": text,
                                "wrap": True,
                                "spacing": "Small"
                            })
                    elif item_type == "bullet_list":
                        items = item.get("items", [])
                        for list_item in items:
                            body_blocks.append({
                                "type": "TextBlock",
                                "text": f"• {list_item}",
                                "wrap": True,
                                "spacing": "Small"
                            })
                    elif item_type == "numbered_list":
                        items = item.get("items", [])
                        for i, list_item in enumerate(items, 1):
                            body_blocks.append({
                                "type": "TextBlock",
                                "text": f"{i}. {list_item}",
                                "wrap": True,
                                "spacing": "Small"
                            })
                    elif item_type == "code_block":
                        body_blocks.append({
                            "type": "TextBlock",
                            "text": f"```\n{item.get('code', '')}\n```",
                            "wrap": True,
                            "fontType": "Monospace",
                            "spacing": "Medium"
                        })
                # Add all non-source paragraphs to the main body
                # for text in other_paragraphs:
                #     body_blocks.append({
                #         "type": "TextBlock",
                #         "text": text,
                #         "wrap": True,
                #         "spacing": "Small"
                #     })
                # Create the source section
                source_container = {
                    "type": "Container",
                    "id": "sourceContainer",
                    "isVisible": False,
                    "style": "emphasis",
                    "bleed": True,
                    "maxHeight": "500px",
                    "isScrollable": True, 
                    "items": []
                }
                # Add Referenced/Calculated paragraphs to the collapsible section if present
                for item in content_items:
                    if item.get("type", "") == "paragraph":
                        text = item.get("text", "")
                        if text.strip().startswith("Referenced:") or text.strip().startswith("Calculated using:"):
                            lines = text.split("\n")
                            # Add the heading ("Referenced:" or "Calculated using:")
                            if lines:
                                source_container["items"].append({
                                    "type": "TextBlock",
                                    "text": lines[0],
                                    "wrap": True,
                                    "spacing": "Small",
                                    "weight": "Bolder"
                                })
                            # For each file/table, add a markdown link as a TextBlock
                            for line in lines[1:]:
                                if line.strip().startswith("-"):
                                    fname = line.strip()[1:].strip()
                                    if fname:
                                        sharepoint_base = "https://dgda.sharepoint.com/:x:/r/sites/CXQAData/_layouts/15/Doc.aspx?sourcedoc=%7B9B3CA3CD-5044-45C7-8A82-0604A1675F46%7D&file={}&action=default&mobileredirect=true"
                                        url = sharepoint_base.format(urllib.parse.quote(fname))
                                        print(f"DEBUG: Adding file link: {fname} -> {url}")
                                        source_container["items"].append({
                                            "type": "TextBlock",
                                            "text": f"[{fname}]({url})",
                                            "wrap": True,
                                            "spacing": "Small"
                                        })
                                else:
                                    # If not a file line, just add as text
                                    source_container["items"].append({
                                        "type": "TextBlock",
                                        "text": line,
                                        "wrap": True,
                                        "spacing": "Small"
                                    })
                # Remove file_names/table_names and code/file blocks from the collapsible section
                # Always add the source line at the bottom of the container
                source_container["items"].append({
                    "type": "TextBlock",
                    "text": f"Source: {source}",
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Accent",
                    "spacing": "Medium",
                })
                body_blocks.append(source_container)
                # Add the show/hide source buttons
                body_blocks.append({
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "id": "showSourceBtn",
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Show Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        },
                        {
                            "type": "Column",
                            "id": "hideSourceBtn",
                            "isVisible": False,
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Hide Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                })
                # Create and send the adaptive card
                adaptive_card = {
                    "type": "AdaptiveCard",
                    "body": body_blocks,
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.5"
                }
                message = Activity(
                    type="message",
                    attachments=[{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": adaptive_card
                    }]
                )
                await turn_context.send_activity(message)
                # Successfully processed JSON, so return early
                return
                
        except (json.JSONDecodeError, KeyError, TypeError):
            # Not JSON or parse failed – we'll handle with the robust fallback below
            cleaned_answer_text = None  # signal for later
            
        # ------------------------------------------------------------------
        # Fallback logic – use the same helper as Testing code.py so we always
        # get an answer, source, and any file names even when the response is
        # not perfect JSON (or we purposely skipped showing the full card).
        # ------------------------------------------------------------------

        if cleaned_answer_text is None:  # JSON branch failed
            main_answer, source_line, files_csv = _parse_answer(answer_text)
            appended_details = ""
        else:
            # JSON parsed but wasn't our structured schema – keep legacy flow
            if match:
                main_answer = match.group(1).strip()
                source_line = match.group(2).strip()
                appended_details = match.group(3) if match.group(3) else ""
            else:
                main_answer = answer_text
                source_line = ""
                appended_details = ""

        if source_line:
            # Create simple text blocks without complex formatting
            body_blocks = [{
                "type": "TextBlock",
                "text": main_answer,
                "wrap": True
            }]
            
            # Create the collapsible source container
            if source_line or appended_details:
                # Create a container that will be toggled
                source_container = {
                    "type": "Container",
                    "id": "sourceContainer",
                    "isVisible": False,
                    "style": "emphasis",
                    "bleed": True,
                    "maxHeight": "500px",
                    "isScrollable": True, 
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": source_line,
                            "wrap": True,
                            "weight": "Bolder",
                            "color": "Accent",
                            "spacing": "Medium",
                        }
                    ]
                }
                
                # Add source details if it exists
                if appended_details:
                    source_container["items"].append({
                        "type": "TextBlock",
                        "text": appended_details.strip(),
                        "wrap": True,
                        "spacing": "Small"
                    })
                    
                body_blocks.append(source_container)
                
                body_blocks.append({
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "id": "showSourceBtn",
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Show Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        },
                        {
                            "type": "Column",
                            "id": "hideSourceBtn",
                            "isVisible": False,
                            "items": [
                                {
                                    "type": "ActionSet",
                                    "actions": [
                                        {
                                            "type": "Action.ToggleVisibility",
                                            "title": "Hide Source",
                                            "targetElements": ["sourceContainer", "showSourceBtn", "hideSourceBtn"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                })


            adaptive_card = {
                "type": "AdaptiveCard",
                "body": body_blocks,
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5"
            }
            
            message = Activity(
                type="message",
                attachments=[{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card
                }]
            )
            await turn_context.send_activity(message)
        else:
            # For simple responses without source, send formatted markdown directly
            # Teams supports some markdown in regular messages
            await turn_context.send_activity(Activity(type="message", text=main_answer))

    except Exception as e:
        error_message = f"An error occurred while processing your request: {str(e)}"
        print(f"Error in bot logic: {e}")
        await turn_context.send_activity(Activity(type="message", text=error_message))

# -----------------------------------------------------------------------------
# Helper functions (borrowed from Testing code.py) to robustly parse Ask_Question
# responses whether they are JSON or legacy plaintext. These allow us to extract
# a clean answer string, the source type, and any referenced files/tables.
# -----------------------------------------------------------------------------
from typing import List, Tuple


def _render_content(blocks: List[dict]) -> str:
    """Render structured LLM content → plain text while **omitting** any internal
    Calculated/Referenced sections (those will be displayed separately)."""
    out: List[str] = []
    skip_mode = False  # True while inside Calc/Ref bullets we plan to skip

    for blk in blocks:
        btype = blk.get("type", "")
        txt = blk.get("text", "")

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
    """Return (clean_answer, source_type, files_used). Works for both the new
    JSON schema and the older plaintext format."""
    # 1) Prefer JSON schema
    try:
        js = json.loads(full)
        answer = _render_content(js.get("content", [])) or full
        source_type = js.get("source", "Unknown")
        det = js.get("source_details", {}) if isinstance(js, dict) else {}
        files = det.get("file_names", []) + det.get("table_names", [])
        files_used = ", ".join(files)
        return answer, source_type, files_used
    except (json.JSONDecodeError, TypeError):
        pass

    # 2) Legacy plain-text splitter
    if "Source:" in full:
        ans, src_part = full.split("Source:", 1)
        ans_clean = ans.strip()
        src_lines = [l.strip() for l in src_part.splitlines() if l.strip()]
        src_type = src_lines[0] if src_lines else "Unknown"
        files = ", ".join(src_lines[1:]) if len(src_lines) > 1 else ""
        return ans_clean, src_type, files

    return full.strip(), "Unknown", ""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
