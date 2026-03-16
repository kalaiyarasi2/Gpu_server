import argparse
import json
import os
from pathlib import Path

from statement_extractor import StatementExtractor


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract bank statement tables to JSON + Excel.")
    parser.add_argument("pdf_path", help="Path to bank statement PDF")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="outputs",
        help="Output directory (default: outputs)",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a short summary to stdout",
    )
    args = parser.parse_args()

    pdf_path = args.pdf_path
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    extractor = StatementExtractor(output_dir=args.output_dir)
    result = extractor.process_pdf(pdf_path)

    if args.print_summary:
        data = result.get("data", {})
        print(json.dumps(
            {
                "session_dir": result.get("session_dir"),
                "json_file": result.get("json_file"),
                "excel_file": result.get("excel_file"),
                "verification_file": result.get("verification_file"),
                "deposits_rows": len(data.get("deposits_and_credits", []) or []),
                "debits_rows": len(data.get("checks_and_other_debits", []) or []),
            },
            indent=2,
        ))
    else:
        print(f"Output written to: {result.get('session_dir')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

