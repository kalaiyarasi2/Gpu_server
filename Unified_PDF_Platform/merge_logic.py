import json
import os
from pathlib import Path
from typing import List, Dict, Any

class DocumentMerger:
    """
    Merges multiple extracted JSON documents based on their schema.
    Supports Insurance Claims (list or {claims: []}) and Vendor Invoices (HEADER/LINE_ITEMS).
    """

    def merge_json_files(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """
        Reads and merges multiple JSON files.
        Returns a flattened list of items (claims or line items).
        """
        merged_data = []

        for path in file_paths:
            if not os.path.exists(path):
                print(f"[Merger][WARN] File not found: {path}")
                continue

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Use helper to extract data based on structure
                extracted_items = self.extract_items(data, os.path.basename(path))
                merged_data.extend(extracted_items)
            except Exception as e:
                print(f"[Merger][ERR] Failed to process {path}: {e}")

        return merged_data

    def extract_items(self, data: Any, source_file: str) -> List[Dict[str, Any]]:
        """
        Extracts items from Insurance Claim structures. 
        Supports {claims: []} or flat lists.
        """
        # Case 1: Insurance Claims {claims: []}
        if isinstance(data, dict) and "claims" in data:
            return [{**item, "SOURCE_FILE": source_file} for item in data["claims"] if isinstance(item, dict)]

        # Case 2: Flat list (Standard for claims)
        if isinstance(data, list):
            return [{**item, "SOURCE_FILE": source_file} for item in data if isinstance(item, dict)]

        return []

if __name__ == "__main__":
    # Quick test if run directly
    import sys
    if len(sys.argv) > 1:
        merger = DocumentMerger()
        result = merger.merge_json_files(sys.argv[1:])
        print(json.dumps(result, indent=2))
