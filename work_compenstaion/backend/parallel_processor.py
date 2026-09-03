
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

# Ensure we can import from the current directory with priority
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ocr_text import OCRPDFExtractor
from text_quality_verifier import TextQualityVerifier
import config

class ParallelPageProcessor:
    """
    Handles parallel processing of PDF pages using ThreadPoolExecutor.
    Uses the strict 5-stage hierarchy for each page.
    """
    
    def __init__(self, pdf_path: str, api_key: str = None, max_workers: int = 4):
        self.pdf_path = pdf_path
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.max_workers = max_workers
        self.ocr_extractor = OCRPDFExtractor(pdf_path, api_key=self.api_key)
        self.verifier = TextQualityVerifier()

    def process_page(self, page_num: int) -> Dict:
        """
        Process a single page using the hierarchical strategy.
        This is designed to be called in parallel.
        """
        start_time = time.time()
        try:
            print(f"🚀 [Thread] Starting Page {page_num}...")
            
            # Use the optimized hierarchical method I implemented in ocr_text.py
            result = self.ocr_extractor.extract_page(
                page_num=page_num,
                psm_mode=getattr(config, 'OCR_PSM_MODE', 3),
                verbose=False
            )
            
            elapsed = time.time() - start_time
            print(f"✅ [Thread] Page {page_num} finished in {elapsed:.2f}s using {result.get('extraction_method', 'unknown')}")
            
            return result
            
        except Exception as e:
            print(f"❌ [Thread] Page {page_num} error: {e}")
            return {
                "page_number": page_num,
                "text": f"\n{'='*80}\nPAGE {page_num} (ERROR)\n{'='*80}\n\n[Failed to extract: {str(e)}]",
                "extraction_method": "failed",
                "confidence": 0.0
            }

    def process_document(self, total_pages: int) -> List[Dict]:
        """
        Orchestrates parallel extraction for the entire document.
        """
        print(f"🔄 Starting parallel extraction for {total_pages} pages using {self.max_workers} workers...")
        start_all = time.time()
        
        results = [None] * total_pages
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Map page numbers to futures
            future_to_page = {
                executor.submit(self.process_page, page_num): page_num 
                for page_num in range(1, total_pages + 1)
            }
            
            for future in as_completed(future_to_page):
                page_num = future_to_page[future]
                try:
                    res = future.result()
                    # Place in correct order
                    results[page_num - 1] = res
                except Exception as e:
                    print(f"Critical error in page {page_num} future: {e}")

        total_elapsed = time.time() - start_all
        print(f"\n✨ Parallel extraction complete in {total_elapsed:.2f}s")
        
        return results

if __name__ == "__main__":
    # Test script
    import sys
    if len(sys.argv) > 1:
        doc_path = sys.argv[1]
        # Quick estimate of pages if pdfplumber not present
        processor = ParallelPageProcessor(doc_path)
        # Assuming 2 pages for quick test if not specified
        processor.process_document(2)
    else:
        print("Usage: python parallel_processor.py <pdf_path>")
