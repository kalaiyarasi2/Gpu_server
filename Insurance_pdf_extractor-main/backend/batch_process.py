"""
Batch Processor for Insurance Extraction
Supports parallel processing of multiple PDF files.

Usage:
    python batch_process.py [input_dir] [output_dir] [--workers 4]

Example:
    python batch_process.py ./sources ./outputs --workers 4
"""

import os
import sys
import json
import csv
import argparse
import time
import concurrent.futures
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from typing import Dict, List, Any

# Load environment variables
load_dotenv()

# Import the extractor
try:
    from chunked_extractor import ChunkedInsuranceExtractor
except ImportError:
    print("❌ Error: Could not import ChunkedInsuranceExtractor from chunked_extractor.py")
    sys.exit(1)


class BatchProcessor:
    def __init__(self, input_dir: str, output_dir: str, max_workers: int = 4):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers
        self.api_key = os.getenv("OPENAI_API_KEY")
        
        if not self.api_key:
            print("❌ Error: OPENAI_API_KEY environment variable is not set.")
            sys.exit(1)
            
        # Initialize one extractor instance per worker usually works, 
        # but the class is stateless enough that we can instantiate it inside the worker
        # or share it if it's thread-safe. 
        # For safety, we'll instantiate inside the processing function or pass it.
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Statistics
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "start_time": None,
            "end_time": None
        }
        
        self.results = []

    def process_file(self, file_path: Path) -> Dict[str, Any]:
        """Process a single file"""
        filename = file_path.name
        print(f"🔄 Processing: {filename}...")
        
        start_time = time.time()
        result_info = {
            "filename": filename,
            "status": "pending",
            "claims_count": 0,
            "processing_time": 0.0,
            "error": None,
            "session_id": None
        }
        
        try:
            # Initialize extractor for this thread
            # (Initializing here ensures thread safety if the client isn't thread safe)
            
            # Create thread-safe unique output directory
            safe_filename = file_path.stem.replace(" ", "_").replace(".", "_")
            timestamp = datetime.now().strftime("%H%M%S") # shorten timestamp for brevity
            thread_output_dir = os.path.join(self.output_dir, f"{safe_filename}_{timestamp}")
            os.makedirs(thread_output_dir, exist_ok=True)

            extractor = ChunkedInsuranceExtractor(self.api_key, output_dir=thread_output_dir)
            
            # Process
            extraction_result = extractor.process_pdf_with_verification(str(file_path))
            
            # Update result info
            elapsed = time.time() - start_time
            
            extracted_schema = extraction_result.get("extracted_schema", {})
            claims = extracted_schema.get("claims", [])
            
            result_info.update({
                "status": "success",
                "claims_count": len(claims),
                "processing_time": round(elapsed, 2),
                "session_id": extraction_result.get("session_id"),
                "output_dir": str(extraction_result.get("session_dir"))
            })
            
            return result_info
            
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e)
            print(f"❌ Failed: {filename} - {error_msg}")
            
            result_info.update({
                "status": "failed",
                "processing_time": round(elapsed, 2),
                "error": error_msg
            })
            return result_info

    def run(self):
        """Run the batch processing"""
        self.stats["start_time"] = datetime.now()
        
        # Find all PDF files
        if not self.input_dir.exists():
            print(f"❌ Error: Input directory '{self.input_dir}' does not exist.")
            return

        pdf_files = list(self.input_dir.glob("*.pdf"))
        self.stats["total"] = len(pdf_files)
        
        if not pdf_files:
            print(f"⚠️ No PDF files found in '{self.input_dir}'")
            return

        print(f"\n🚀 Starting batch processing of {len(pdf_files)} files")
        print(f"📂 Input: {self.input_dir}")
        print(f"📂 Output: {self.output_dir}")
        print(f"⚡ Workers: {self.max_workers}")
        print("=" * 60)

        # Process using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_file = {executor.submit(self.process_file, pdf): pdf for pdf in pdf_files}
            
            # Process results as they complete
            for future in concurrent.futures.as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    result = future.result()
                    self.results.append(result)
                    
                    if result["status"] == "success":
                        self.stats["success"] += 1
                        print(f"✅ Completed: {result['filename']} ({result['claims_count']} claims) in {result['processing_time']}s")
                    else:
                        self.stats["failed"] += 1
                except Exception as exc:
                    print(f"❌ Unhandled exception for {file_path.name}: {exc}")
                    self.stats["failed"] += 1

        self.stats["end_time"] = datetime.now()
        self._generate_reports()

    def _generate_reports(self):
        """Generate summary reports"""
        timestamp = self.stats["start_time"].strftime("%Y%m%d_%H%M%S")
        
        # 1. JSON Report
        report_data = {
            "summary": {
                "total_files": self.stats["total"],
                "success": self.stats["success"],
                "failed": self.stats["failed"],
                "duration_seconds": (self.stats["end_time"] - self.stats["start_time"]).total_seconds(),
                "timestamp": timestamp
            },
            "results": self.results
        }
        
        json_path = self.output_dir / f"batch_report_{timestamp}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2)
            
        # 2. CSV Summary
        csv_path = self.output_dir / f"batch_summary_{timestamp}.csv"
        fieldnames = ["filename", "status", "claims_count", "processing_time", "session_id", "error"]
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for res in self.results:
                # Filter to only known fields
                row = {k: res.get(k) for k in fieldnames}
                writer.writerow(row)

        print("\n" + "=" * 60)
        print("BATCH PROCESSING COMPLETE")
        print("=" * 60)
        print(f"Total Files: {self.stats['total']}")
        print(f"Success:     {self.stats['success']}")
        print(f"Failed:      {self.stats['failed']}")
        print(f"Duration:    {report_data['summary']['duration_seconds']:.2f}s")
        print(f"\n📄 Reports saved to:")
        print(f"  - {json_path}")
        print(f"  - {csv_path}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Batch process PDF insurance forms")
    parser.add_argument("input_dir", nargs="?", default="sources", help="Directory containing PDF files")
    parser.add_argument("output_dir", nargs="?", default="outputs", help="Directory to save outputs")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker threads (default: 4)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_dir):
        # Create sources dir if it doesn't exist, just to be helpful for first run
        os.makedirs(args.input_dir, exist_ok=True)
        print(f"Created input directory: {args.input_dir}")
        print("Please place PDF files in this directory and run again.")
        return

    processor = BatchProcessor(args.input_dir, args.output_dir, args.workers)
    processor.run()


if __name__ == "__main__":
    main()
