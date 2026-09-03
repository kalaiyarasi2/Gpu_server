"""
connector.py - Real-Time Outlook Watcher → Auto-Download → Extraction Pipeline
-----------------------------------------------------------------------------

BEHAVIOR:
  - Polls Outlook every N seconds for NEW unread emails with PDF attachments
  - Already-processed emails are tracked in processed_ids.json -> NEVER re-processed
  - New PDFs downloaded to downloads/ folder immediately on arrival
  - Each downloaded PDF is passed straight into the extraction model
  - Email marked as read after all its PDFs are extracted
  - Results saved to results/extraction_<timestamp>.csv
  - downloads/ cleaned up after extraction
"""

from __future__ import annotations

import os
import sys
import io
import glob
import time
import base64
import argparse
import logging
import requests
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from dotenv import load_dotenv

# ── Mock 'auth' module to prevent running Gmail authentication on import ──────
from unittest.mock import MagicMock
class MockAuth:
    gmail = MagicMock()

sys.modules['auth'] = MockAuth

# Reconfigure stdout/stderr for UTF-8 support on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Setup path imports for Outlook_Agent
CURRENT_DIR = Path(__file__).parent.resolve()
OUTLOOK_AGENT_DIR = CURRENT_DIR.parent.parent / "Outlook_Agent"
if str(OUTLOOK_AGENT_DIR) not in sys.path:
    sys.path.append(str(OUTLOOK_AGENT_DIR))

# Import main components from Email_pipeline main.py
from main import extraction_phase, save_and_cleanup, DOWNLOAD_DIR, RESULTS_DIR

try:
    from outlook_agent_module import OutlookAgentModule, _mark_read, _load_processed_ids, _save_processed_ids
except ImportError:
    OutlookAgentModule = None
    _mark_read = None
    _load_processed_ids = None
    _save_processed_ids = None

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("outlook_connector_pipeline")


def get_outlook_agent() -> OutlookAgentModule | None:
    """Instantiate and return the OutlookAgentModule."""
    if OutlookAgentModule is None:
        log.error("OutlookAgentModule class could not be found or imported.")
        return None
    try:
        return OutlookAgentModule()
    except Exception as exc:
        log.error("Failed to initialize OutlookAgentModule: %s", exc)
        return None


def send_email_with_results_outlook(token: str, recipient: str, subject: str, body: str, attachment_paths: list[str]) -> bool:
    """
    Sends email with results attachments using Outlook MS Graph API.
    """
    try:
        log.info("[EMAIL] Preparing to send results via Outlook to %s...", recipient)
        
        attachments_payload = []
        for path in attachment_paths:
            if not os.path.exists(path):
                log.warning("[EMAIL] Attachment not found: %s", path)
                continue
            
            p = Path(path)
            file_bytes = p.read_bytes()
            b64_content = base64.b64encode(file_bytes).decode('utf-8')
            
            attachments_payload.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": p.name,
                "contentBytes": b64_content
            })
            
        message_payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": recipient
                        }
                    }
                ],
                "attachments": attachments_payload
            }
        }
        
        url = "https://graph.microsoft.com/v1.0/me/sendMail"
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json=message_payload
        )
        
        if resp.status_code in (200, 202):
            log.info("[EMAIL] [OK] Results sent successfully via Outlook.")
            return True
        else:
            log.error("[EMAIL] [FAIL] Outlook sendMail failed (%d): %s", resp.status_code, resp.text)
            return False
            
    except Exception as e:
        log.error("[EMAIL] [FAIL] Failed to send email via Outlook: %s", e)
        return False


def sync_outlook_attachments(mark_read: bool = True) -> tuple[list[str], str]:
    """
    Fetches unread Outlook emails, downloads PDF attachments,
    marks emails as read, and returns the list of newly downloaded PDF file paths
    along with the MS Graph access token used.
    """
    agent = get_outlook_agent()
    if not agent:
        log.error("Outlook agent not available. Skipping attachment sync.")
        return [], ""

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    new_pdfs = []
    token = ""

    try:
        # Obtain MS Graph token
        token = agent.get_access_token()
        
        # Load previously processed email IDs
        processed_ids_file = agent.processed_ids_file
        if _load_processed_ids:
            processed_ids = _load_processed_ids(processed_ids_file)
        else:
            processed_ids = set()

        # Fetch unread emails
        emails = agent.fetch_unread_emails()
        if not emails:
            log.info("[SCAN] No new unread emails found in Outlook.")
            return [], token

        for email in emails:
            email_id = email["id"]
            attachments = email.get("attachments", [])
            
            if not attachments:
                if mark_read and _mark_read:
                    _mark_read(token, email_id)
                processed_ids.add(email_id)
                continue

            for att in attachments:
                filename = att["filename"]
                if not filename.lower().endswith(".pdf"):
                    continue

                content_bytes = base64.b64decode(att["content_bytes"])
                dest_file = Path(DOWNLOAD_DIR) / filename
                
                # Write to downloads folder
                dest_file.write_bytes(content_bytes)
                log.info("[DOWNLOAD] Saved Outlook attachment: %s", filename)
                new_pdfs.append(str(dest_file))

            # Mark email as read and add to processed list
            if mark_read and _mark_read:
                _mark_read(token, email_id)
            processed_ids.add(email_id)

        # Save processed IDs
        if _save_processed_ids:
            _save_processed_ids(processed_ids, processed_ids_file)

        return new_pdfs, token

    except Exception as exc:
        log.error("Error during Outlook attachment sync: %s", exc, exc_info=True)
        return [], token


def poll_outlook(cleanup: bool = True, mark_read: bool = True) -> int:
    """
    Checks Outlook, downloads new PDFs, runs the extraction pipeline, and cleans up.
    """
    print(f"\n[SCAN] [{datetime.now().strftime('%H:%M:%S')}]  Checking Outlook for new unread PDFs...", flush=True)

    # 1. Collect any existing PDFs in downloads/ from a previous run
    existing_pdfs = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf")))
    if existing_pdfs:
        log.info("[RESUME] Found %d existing PDF(s) in downloads/ to process: %s", 
                 len(existing_pdfs), [os.path.basename(p) for p in existing_pdfs])

    # 2. Sync new PDFs from Outlook
    newly_downloaded, token = sync_outlook_attachments(mark_read=mark_read)

    # 3. Combine both lists
    all_pdfs = sorted(list(set(existing_pdfs) | set(newly_downloaded)))

    if not all_pdfs:
        print("   [EMPTY] No new or existing PDFs — Outlook inbox is clear.", flush=True)
        return 0

    # 4. Run the extraction model on PDFs
    print(f"\n[PROCESS] {len(all_pdfs)} PDF(s) ready for extraction...", flush=True)
    results = extraction_phase(all_pdfs)

    # 5. Send results via email
    if results and token:
        attachment_paths = []
        for r in results:
            if r.get("excel") and os.path.exists(r["excel"]):
                attachment_paths.append(r["excel"])
            if r.get("json") and os.path.exists(r["json"]):
                attachment_paths.append(r["json"])
        
        if attachment_paths:
            recipient = os.getenv("RECIPIENT_EMAIL", "althafm3017@gmail.com")
            subject   = f"Outlook Extraction Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            body      = f"Attached are the extraction results for {len(results)} file(s)."
            send_email_with_results_outlook(token, recipient, subject, body, list(set(attachment_paths)))

    # 6. Save results to CSV and cleanup
    save_and_cleanup(results, all_pdfs, cleanup=cleanup)

    filenames = [os.path.basename(p) for p in all_pdfs]
    from tracker import mark_processed as _mark
    _mark("outlook_batch_" + datetime.now().strftime("%Y%m%d_%H%M%S"), filenames)
    log.info("Tracker Update: %d files processed in this cycle.", len(filenames))

    return len(all_pdfs)


def watch_outlook(interval: int = 60, cleanup: bool = True, mark_read: bool = True):
    """
    Continuous polling of Outlook emails at set intervals.
    """
    print(f"\n{'='*60}")
    print(f"[WATCH] Outlook PDF Watcher  -  Base check every {interval}s")
    print(f"{'='*60}\n")

    current_interval = interval
    consecutive_empty = 0
    MAX_INTERVAL = 300  # 5 minutes

    while True:
        try:
            # Check if automation toggle is on
            if os.getenv("EMAIL_AUTOMATION", "True").lower() == "false":
                print("[PAUSED] Email automation is disabled. Waiting for reactivation...")
                time.sleep(30)
                continue
            
            processed_count = poll_outlook(cleanup=cleanup, mark_read=mark_read)
            
            # Backoff logic
            if processed_count == 0:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    current_interval = min(current_interval * 2, MAX_INTERVAL)
                    log.info("[BACKOFF] Outlook inbox empty. Increasing interval to %ds", current_interval)
            else:
                if consecutive_empty > 0:
                    log.info("[RESET] New emails found. Resetting interval to %ds", interval)
                consecutive_empty = 0
                current_interval = interval

            print(f"[WAIT] Next check in {current_interval}s  (Ctrl+C to stop)\n")
            time.sleep(current_interval)

        except KeyboardInterrupt:
            print("\n[STOP] Watcher stopped by user.")
            break
        except SystemExit as se:
            log.error("SystemExit caught. Retrying watcher in %ds...", interval)
            time.sleep(interval)
        except Exception as e:
            log.error("Unexpected error during poll: %s", e)
            log.info("Retrying in %ds...", interval)
            time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Outlook Real-Time PDF → Extraction Pipeline")
    parser.add_argument("--run-once",      action="store_true", help="Single pass then exit")
    parser.add_argument("--interval",      type=int, default=30, help="Poll interval in seconds (default: 30)")
    parser.add_argument("--no-cleanup",    action="store_true", help="Keep PDFs in downloads/ after extraction")
    parser.add_argument("--no-mark-read",  action="store_true", help="Don't mark emails as read after processing")
    args = parser.parse_args()

    # Verify Outlook module exists
    if OutlookAgentModule is None:
        log.critical("OutlookAgentModule is not installed or configured correctly.")
        return 1

    try:
        if args.run_once:
            poll_outlook(cleanup=not args.no_cleanup, mark_read=not args.no_mark_read)
        else:
            watch_outlook(
                interval=args.interval,
                cleanup=not args.no_cleanup,
                mark_read=not args.no_mark_read,
            )
        return 0
    except KeyboardInterrupt:
        print("\n[STOP] Stopped.")
        return 0
    except Exception as exc:
        log.critical("Pipeline crashed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
