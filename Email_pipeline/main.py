"""
main.py - Real-Time Gmail Watcher → Auto-Download → Extraction Pipeline
------------------------------------------------------------------------

BEHAVIOR:
  - Polls Gmail every N seconds for NEW unread emails with PDF attachments
  - Already-processed emails are tracked in processed_ids.json -> NEVER re-sent
  - Already-read emails (no UNREAD label) are automatically skipped
  - New PDFs downloaded to downloads/ folder immediately on arrival
  - Each downloaded PDF is passed straight into your extraction model
  - Email marked as read after all its PDFs are extracted
  - Results saved to results/extraction_<timestamp>.csv
  - downloads/ cleaned up after extraction
"""

import os
import sys
import io
import glob
import time
import argparse
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv

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

from tools import get_new_unread_pdf_emails, get_attachments, download_pdf, mark_as_read, DOWNLOAD_DIR, is_rate_limited, seconds_until_ok, send_email_with_results
from extraction_model import ExtractionModel
from tracker import get_processed_ids, mark_processed, summary as tracker_summary

load_dotenv()

RESULTS_DIR = "results"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR,  exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — Agent: find new unread emails, download PDFs, mark as read
# ─────────────────────────────────────────────────────────────────────────────
def agent_phase(mark_read: bool = True) -> list[str]:
    """
    Run LangGraph agent to:
      1. Call get_new_unread_pdf_emails (passes already-processed IDs automatically)
      2. Call get_attachments for each new email
      3. Call download_pdf for every PDF found
      4. Call mark_as_read after all PDFs from an email are downloaded

    Returns list of pdf_paths.
    """
    already_done = get_processed_ids()

    # Snapshot downloads/ before agent runs so we know what's new
    before = set(glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf")))

    tools_list = [get_new_unread_pdf_emails, get_attachments, download_pdf]
    if mark_read:
        tools_list.append(mark_as_read)

    agent = create_react_agent(
        ChatOpenAI(model="gpt-4o-mini", temperature=0),
        tools_list,
    )

    mark_instruction = (
        "After downloading all PDFs from an email, immediately call mark_as_read with that email's id."
        if mark_read else "Do NOT call mark_as_read."
    )

    system = (
        "You are a Gmail PDF monitoring agent. You must process EVERY attachment found in an email.\n"
        "Follow these steps exactly:\n"
        "1. Call get_new_unread_pdf_emails with the processed_ids list provided.\n"
        "   If count is 0, stop and report 'No new emails'.\n"
        "2. For each email returned, call get_attachments with its id.\n"
        "3. FROM THE JSON RETURNED BY get_attachments, you MUST call download_pdf for EVERY attachment listed.\n"
        "   - Compare the number of attachments found (attachment_count) with your successful downloads.\n"
        "   - DO NOT SKIP any file.\n"
        f"4. {mark_instruction}\n"
        "5. ONLY call mark_as_read AFTER you have successfully called download_pdf for ALL attachments in that message.\n"
        "CRITICAL: You are an automated system. DO NOT ask interactive questions. DO NOT wait for user input. "
        "Report total emails processed and total PDFs downloaded."
    )

    try:
        result = agent.invoke({
            "messages": [
                SystemMessage(content=system),
                HumanMessage(
                    content=f"Process new unread PDF emails. Already processed IDs to skip: {already_done}"
                ),
            ]
        })
        
        # Robustly handle agent summary (avoid encoding crashes)
        summary_msg = result["messages"][-1].content if result.get("messages") else ""
        if summary_msg:
            try:
                log.info("Agent: %s", summary_msg)
            except UnicodeEncodeError:
                log.info("Agent: [Summary contains non-ascii characters, suppressed to avoid crash]")
    except BaseException as be:
        log.error("Agent blocked or exited by BaseException (type=%s): %s", type(be).__name__, str(be))
        if isinstance(be, SystemExit):
             log.error("CRITICAL: SystemExit detected inside agent phase!")
        raise be
    except Exception as ae:
        log.error("Agent encountered an error: %s", str(ae))

    # Determine which new files appeared in downloads/
    log.info("[TRACE] Comparing downloads/ before and after...")
    after    = set(glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf")))
    new_pdfs = sorted(after - before)
    
    if new_pdfs:
        log.info("[VERIFY] Successfully downloaded %d new PDF(s): %s", len(new_pdfs), new_pdfs)
    else:
        log.info("[VERIFY] No new PDFs downloaded in this phase.")
        
    log.info("[TRACE] agent_phase returning %d PDFs", len(new_pdfs))
    sys.stdout.flush()
    sys.stderr.flush()
    return new_pdfs


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Extraction: pass each new PDF directly to your model
# ─────────────────────────────────────────────────────────────────────────────
def extraction_phase(pdf_paths: list[str]) -> list[dict]:
    """
    Send each PDF path directly into ExtractionModel.extract().
    Uses ThreadPoolExecutor for parallel processing.
    """
    if not pdf_paths:
        return []

    extractor = ExtractionModel()
    results   = []
    
    max_workers = min(len(pdf_paths), 10)
    print(f"\n   [PARALLEL] Starting extraction for {len(pdf_paths)} file(s) with {max_workers} workers...")

    def process_file(pdf_path):
        fname = os.path.basename(pdf_path)
        print(f"   [SEND] Sending to extraction model: {fname}")
        try:
            data = extractor.extract(pdf_path)
            data["source_file"]  = fname
            data["processed_at"] = datetime.now().isoformat(timespec="seconds")
            log.info("[OK] Extracted  %s  status=%s", fname, data.get("status", "ok"))
            return data
        except Exception as e:
            log.error("[FAIL] Failed     %s  error=%s", fname, e)
            return {
                "source_file":  fname,
                "processed_at": datetime.now().isoformat(timespec="seconds"),
                "status":       "error",
                "error":        str(e),
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pdf = {executor.submit(process_file, p): p for p in pdf_paths}
        for future in as_completed(future_to_pdf):
            results.append(future.result())

    return results


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — Save results + update tracker + cleanup
# ─────────────────────────────────────────────────────────────────────────────
def save_and_cleanup(results: list[dict], pdf_paths: list[str], cleanup: bool = True):
    if not results:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path  = os.path.join(RESULTS_DIR, f"extraction_{timestamp}.csv")
    df        = pd.DataFrame(results)
    df.to_csv(csv_path, index=False)
    print(f"\n   [SAVE] Results -> {csv_path}")
    
    # Clean up downloaded PDFs
    if cleanup:
        for path in pdf_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        log.info("[CLEANUP] Cleaned %d PDF(s) from downloads/", len(pdf_paths))


# ─────────────────────────────────────────────────────────────────────────────
def poll(cleanup: bool = True, mark_read: bool = True) -> int:
    print(f"\n[SCAN] [{datetime.now().strftime('%H:%M:%S')}]  Checking Gmail for new unread PDFs...", flush=True)
    log.info("[TRACE] Starting agent_phase...")
    # 1. Collect any "orphaned" PDFs already in downloads/ from a previous interrupted run
    existing_pdfs = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf")))
    if existing_pdfs:
        log.info("[RESUME] Found %d existing PDF(s) in downloads/ to process: %s", 
                 len(existing_pdfs), [os.path.basename(p) for p in existing_pdfs])

    # 2. Run agent to find/download NEW emails
    try:
        newly_downloaded = agent_phase(mark_read=mark_read)
    except BaseException as b:
        log.error("[CRITICAL] poll caught BaseException from agent_phase: %s", type(b).__name__)
        raise b

    # 3. Combine both lists (avoid duplicates just in case)
    all_pdfs = sorted(list(set(existing_pdfs) | set(newly_downloaded)))

    if not all_pdfs:
        print("   [EMPTY] No new or existing PDFs — inbox is clear.", flush=True)
        return 0

    # 4. Save results + cleanup
    print(f"\n[PROCESS] {len(all_pdfs)} PDF(s) ready for extraction...", flush=True)
    results = extraction_phase(all_pdfs)
    
    # Send results via email before potential cleanup
    if results:
        attachment_paths = []
        for r in results:
            if r.get("excel") and os.path.exists(r["excel"]):
                attachment_paths.append(r["excel"])
            if r.get("json") and os.path.exists(r["json"]):
                attachment_paths.append(r["json"])
        
        if attachment_paths:
            recipient = os.getenv("RECIPIENT_EMAIL", "althafm3017@gmail.com")
            subject   = f"Extraction Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            body      = f"Attached are the extraction results for {len(results)} file(s)."
            send_email_with_results(recipient, subject, body, list(set(attachment_paths)))

    save_and_cleanup(results, all_pdfs, cleanup=cleanup)

    filenames = [os.path.basename(p) for p in all_pdfs]
    from tracker import mark_processed as _mark
    # We use a batch tag, but for individual email tracking the agent already calls mark_as_read.
    # The tracker here just helps summarize the work done in this process cycle.
    _mark("batch_" + datetime.now().strftime("%Y%m%d_%H%M%S"), filenames)

    ts_info = tracker_summary()
    log.info("Tracker Update: %d files processed in this cycle.", len(filenames))

    return len(all_pdfs)


# ─────────────────────────────────────────────────────────────────────────────
# WATCH MODE — continuous polling
# ─────────────────────────────────────────────────────────────────────────────
def watch(interval: int = 30, cleanup: bool = True, mark_read: bool = True):
    print(f"\n{'='*60}")
    print(f"[WATCH] Gmail PDF Watcher  -  checking every {interval}s")
    print(f"{'='*60}\n")

    while True:
        try:
            # ── Skip this poll if we are still within a rate-limit window ──
            if is_rate_limited():
                wait_left = seconds_until_ok()
                print(f"[SKIP] Still rate-limited by Gmail — {wait_left:.0f}s remaining. "
                      f"Next check in {interval}s", flush=True)
            else:
                poll(cleanup=cleanup, mark_read=mark_read)
            print(f"[WAIT] Next check in {interval}s  (Ctrl+C to stop)\n")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[STOP] Watcher stopped.")
            break
        except SystemExit as se:
            log.error("SystemExit caught! This usually means an extraction script called exit(). Code: %s", se.code)
            log.info("Retrying watcher in %ds...", interval)
            time.sleep(interval)
        except Exception as e:
            try:
                log.error("Unexpected error during poll: %s", str(e))
            except:
                log.error("Unexpected error during poll (logging failed due to encoding)")
            log.info("Retrying in %ds…", interval)
            time.sleep(interval)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Gmail Real-Time PDF → Extraction Pipeline")
        parser.add_argument("--run-once",      action="store_true", help="Single pass then exit")
        parser.add_argument("--interval",      type=int, default=30, help="Poll interval in seconds (default: 30)")
        parser.add_argument("--no-cleanup",    action="store_true", help="Keep PDFs in downloads/ after extraction")
        parser.add_argument("--no-mark-read",  action="store_true", help="Don't mark emails as read after processing")
        args = parser.parse_args()

        try:
            import auth  # noqa
            print("[OK] Gmail authenticated")
        except Exception as e:
            print(f"[FAIL] Auth failed: {e}")
            sys.exit(1)

        if args.run_once:
            poll(cleanup=not args.no_cleanup, mark_read=not args.no_mark_read)
        else:
            watch(
                interval=args.interval,
                cleanup=not args.no_cleanup,
                mark_read=not args.no_mark_read,
            )
    except BaseException as global_e:
        if isinstance(global_e, (KeyboardInterrupt, SystemExit)):
             if isinstance(global_e, KeyboardInterrupt):
                 print("\n[STOP] Watcher stopped by user (Ctrl+C)")
             else:
                 print(f"\n[CRITICAL] Watcher exited with SystemExit(code={getattr(global_e, 'code', 'unknown')})")
        else:
             print(f"\n[CRITICAL] Watcher crashed with {type(global_e).__name__}: {global_e}")
             import traceback
             traceback.print_exc()
        sys.exit(getattr(global_e, 'code', 1) if isinstance(global_e, SystemExit) else 1)
