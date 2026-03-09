"""
tools.py - Gmail Tools
  • get_new_unread_pdf_emails  – only emails NOT already processed
  • get_attachments            – list PDFs on a message
  • download_pdf               – save to downloads/
  • mark_as_read               – remove UNREAD label after processing
"""

import os
import re
import time
import base64
import json
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone
from googleapiclient.errors import HttpError
from langchain_core.tools import tool
from auth import gmail

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── Rate-limit state (shared across tools) ───────────────────────────────────
# When a 429 is hit, RATE_LIMIT_UNTIL is set to the UTC Unix timestamp after
# which it is safe to retry.  main.py inspects this to skip polls.
_rate_lock      = threading.Lock()
RATE_LIMIT_UNTIL: float = 0.0   # epoch seconds; 0 = not rate-limited


def _set_rate_limit(until_epoch: float):
    global RATE_LIMIT_UNTIL
    with _rate_lock:
        RATE_LIMIT_UNTIL = until_epoch


def is_rate_limited() -> bool:
    """Return True if we are still within a rate-limit window."""
    with _rate_lock:
        return time.time() < RATE_LIMIT_UNTIL


def seconds_until_ok() -> float:
    """Return how many seconds remain in the current rate-limit window (0 if none)."""
    with _rate_lock:
        remaining = RATE_LIMIT_UNTIL - time.time()
        return max(0.0, remaining)


def _parse_retry_after(error_msg: str) -> float:
    """
    Try to extract the ISO-8601 datetime from a Gmail 429 message like:
      "User-rate limit exceeded. Retry after 2026-03-05T05:58:21.796Z"
    Returns the epoch timestamp of when retrying is safe, or 0 if not found.
    """
    match = re.search(r"Retry after (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)", error_msg)
    if match:
        try:
            ts_str = match.group(1)
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            pass
    return 0.0


# Transient errors that are safe to retry (proxy drops idle connections, etc.)
_RETRYABLE_ERRORS = (
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    ConnectionError,
)
_RETRYABLE_MESSAGES = ("remote end closed connection", "connection was forcibly closed")

_MAX_RETRIES = 3


def _gmail_execute(request):
    """
    Execute a Gmail API request with automatic retry for transient connection
    errors (e.g. 'Remote end closed connection without response' caused by a
    corporate proxy dropping idle TCP connections between agent tool calls).

    On a 429 rate-limit error, records the Retry-After window and raises
    immediately — the outer watcher loop skips polls until the window clears.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return request.execute()

        except HttpError as e:
            if e.resp.status == 429:
                msg = str(e)
                retry_at = _parse_retry_after(msg)
                if retry_at:
                    wait = max(retry_at - time.time(), 1.0)
                    _set_rate_limit(retry_at)
                    print(f"      [RATE-LIMIT] 429 from Gmail — rate-limited for {wait:.0f}s "
                          f"(until {datetime.fromtimestamp(retry_at).strftime('%H:%M:%S')}). "
                          f"Skipping until window clears.")
                else:
                    _set_rate_limit(time.time() + 60)
                    print(f"      [RATE-LIMIT] 429 from Gmail (no Retry-After) — "
                          f"backing off 60s.")
            raise  # non-429 HTTP errors and 429 always propagate immediately

        except Exception as e:
            # Retry only on transient connection-level errors
            err_str = str(e).lower()
            is_retryable = (
                isinstance(e, _RETRYABLE_ERRORS)
                or any(msg in err_str for msg in _RETRYABLE_MESSAGES)
            )
            if is_retryable and attempt < _MAX_RETRIES:
                wait = 2 ** attempt          # 1s, 2s, 4s
                print(f"      [RETRY] Transient connection error (attempt {attempt + 1}/{_MAX_RETRIES}), "
                      f"retrying in {wait}s — {e}")
                time.sleep(wait)
                last_exc = e
                continue
            raise

    raise last_exc  # exhausted retries


@tool
def get_new_unread_pdf_emails(processed_ids: list) -> str:
    """
    Fetch unread emails with PDF attachments that have NOT been processed before.

    Args:
        processed_ids: List of message IDs already processed (from tracker).

    Returns:
        JSON — list of {id, subject, from, date} for genuinely new emails only.
    """
    try:
        resp = _gmail_execute(
            gmail.users().messages().list(
                userId="me",
                q="is:unread has:attachment filename:pdf",
                maxResults=50,
            )
        )

        all_msgs = resp.get("messages", [])
        # Filter out already-processed IDs
        new_msgs = [m for m in all_msgs if m["id"] not in processed_ids]

        if not new_msgs:
            return json.dumps({"count": 0, "emails": []})

        emails = []
        for ref in new_msgs:
            msg = _gmail_execute(
                gmail.users().messages().get(
                    userId="me", id=ref["id"],
                    format="metadata",
                    metadataHeaders=["Subject", "From", "Date"],
                )
            )
            hdrs = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            emails.append({
                "id":      msg["id"],
                "subject": hdrs.get("Subject", "(no subject)"),
                "from":    hdrs.get("From", ""),
                "date":    hdrs.get("Date", ""),
            })

        return json.dumps({"count": len(emails), "emails": emails}, indent=2)
    except Exception as e:
        print(f"      [TOOL_ERR] get_new_unread_pdf_emails: {e}")
        return json.dumps({"error": str(e)})


@tool
def get_attachments(message_id: str) -> str:
    """
    List all PDF attachments in a Gmail message.

    Args:
        message_id: Gmail message ID.

    Returns:
        JSON list of {attachmentId, filename, size_kb}.
    """
    try:
        msg   = _gmail_execute(
            gmail.users().messages().get(
                userId="me", id=message_id, format="full"
            )
        )
        pdfs  = []

        def find_pdfs(initial_parts):
            stack = [initial_parts]
            while stack:
                parts = stack.pop()
                if not parts:
                    continue
                    
                for part in parts:
                    # Add sub-parts to stack instead of recursing
                    if part.get("parts"):
                        stack.append(part["parts"])
                    
                    fname  = part.get("filename", "")
                    body   = part.get("body", {})
                    att_id = body.get("attachmentId")
                    
                    # Check if it's a PDF attachment
                    if att_id and fname.lower().endswith(".pdf"):
                        pdfs.append({
                            "attachmentId": att_id,
                            "filename":     fname,
                            "size_kb":      round(body.get("size", 0) / 1024, 1),
                        })

        payload = msg.get("payload", {})
        # Check root level and nested parts
        if "parts" in payload:
            find_pdfs(payload["parts"])
        elif payload.get("filename", "").lower().endswith(".pdf"):
            # Handle single-part PDF messages
            body = payload.get("body", {})
            if body.get("attachmentId"):
                pdfs.append({
                    "attachmentId": body["attachmentId"],
                    "filename":     payload["filename"],
                    "size_kb":      round(body.get("size", 0) / 1024, 1),
                })

        return json.dumps({
            "message_id": message_id, 
            "attachment_count": len(pdfs),
            "attachments": pdfs
        }, indent=2)
    except Exception as e:
        print(f"      [TOOL_ERR] get_attachments: {e}")
        return json.dumps({"error": str(e)})


@tool
def download_pdf(message_id: str, attachment_id: str, filename: str = "") -> str:
    """
    Download a PDF attachment into the downloads/ folder.

    Args:
        message_id:    Gmail message ID.
        attachment_id: Attachment ID from get_attachments.
        filename:      Original filename to preserve.

    Returns:
        JSON with saved file path or error.
    """
    try:
        if not filename:
            filename = f"pdf_{attachment_id[:10]}.pdf"
        filename  = os.path.basename(filename)
        save_path = os.path.join(DOWNLOAD_DIR, filename)

        # Avoid overwriting duplicates
        if os.path.exists(save_path):
            base, ext = os.path.splitext(filename)
            save_path = os.path.join(DOWNLOAD_DIR, f"{base}_{attachment_id[:6]}{ext}")

        att       = _gmail_execute(
            gmail.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            )
        )
        data      = base64.urlsafe_b64decode(att["data"].encode("utf-8"))

        with open(save_path, "wb") as f:
            f.write(data)

        print(f"      [SAVE] Downloaded -> {save_path} ({len(data)/1024:.1f} KB)")
        return json.dumps({"status": "ok", "path": save_path})
    except Exception as e:
        print(f"      [TOOL_ERR] download_pdf: {e}")
        return json.dumps({"status": "error", "error": str(e)})


@tool
def mark_as_read(message_id: str) -> str:
    """
    Remove the UNREAD label from a Gmail message.

    Args:
        message_id: Gmail message ID.
    """
    try:
        _gmail_execute(
            gmail.users().messages().modify(
                userId="me", id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            )
        )
        return json.dumps({"status": "marked_read", "id": message_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


def send_email_with_results(recipient: str, subject: str, body: str, attachment_paths: list[str]) -> bool:
    """
    Constructs and sends a MIME multipart email with attachments via Gmail API.
    
    Args:
        recipient: Receiver email address.
        subject: Email subject.
        body: Plain text body.
        attachment_paths: List of absolute paths to files to attach.
        
    Returns:
        bool: True if sent successfully, False otherwise.
    """
    try:
        print(f"\n   [EMAIL] Preparing to send results to {recipient}...")
        message = MIMEMultipart()
        message['to'] = recipient
        message['subject'] = subject

        message.attach(MIMEText(body, 'plain'))

        for path in attachment_paths:
            if not os.path.exists(path):
                print(f"      [WARN] Attachment not found, skipping: {path}")
                continue
                
            fname = os.path.basename(path)
            print(f"      [ATTACH] Attaching {fname}...")
            
            with open(path, "rb") as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{fname}"'
            )
            message.attach(part)

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        
        _gmail_execute(
            gmail.users().messages().send(
                userId="me",
                body={'raw': raw_message}
            )
        )
        
        print(f"   [EMAIL] [OK] Results sent successfully to {recipient}.")
        return True
        
    except Exception as e:
        print(f"   [EMAIL] [FAIL] Failed to send email: {e}")
        return False
