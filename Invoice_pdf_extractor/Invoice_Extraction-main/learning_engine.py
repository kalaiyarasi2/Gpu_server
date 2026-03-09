import re
import os
import json
from typing import List, Dict, Optional

TRAINING_DIR = os.path.join(os.path.dirname(__file__), "training_data")

def discover_examples(text: str) -> str:
    """
    Search training_data/ for relevant examples based on keywords in the text.
    Returns a formatted Few-Shot prompt string.
    """
    if not os.path.exists(TRAINING_DIR):
        return ""

    relevant_examples = []
    
    try:
        for filename in os.listdir(TRAINING_DIR):
            if filename.endswith(".json"):
                path = os.path.join(TRAINING_DIR, filename)
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Check if any keyword matches the document text
                    keywords = data.get("CARRIER_KEYWORDS", [])
                    if any(kw.lower() in text.lower() for kw in keywords):
                        print(f"  [LEARNING] Found relevant training example: {filename}")
                        relevant_examples.extend(data.get("EXAMPLES", []))
    except Exception as e:
        print(f"  [LEARNING][ERROR] Failed to load training examples: {e}")

    if not relevant_examples:
        return ""

    # Format into a prompt string
    prompt_snippet = """
### TRAINING EXAMPLES (LEARNED PATTERNS):
- The following examples show how to MAP fields for this carrier format.
- **CRITICAL**: Only use the MAPPING LOGIC from these examples. 
- **DO NOT COPY** the numerical values (Premiums, IDs) from the examples.
- You **MUST** extract the EXACT numbers currently visible in the document text.
"""
    for i, ex in enumerate(relevant_examples[:3]): # Limit to top 3 to keep prompt size manageable
        prompt_snippet += f"Example {i+1}:\n"
        prompt_snippet += f"Raw Text: \"{ex['RAW_TEXT']}\"\n"
        prompt_snippet += f"Desired Output: {json.dumps(ex['MAPPING'])}\n\n"
        
    return prompt_snippet

def should_trigger_refinement(extracted_data: Dict, raw_text: str) -> tuple:
    """
    Decide if the extraction quality is low enough to warrant a second pass.
    Returns (should_refine, target_total, current_sum)
    """
    line_items = extracted_data.get("LINE_ITEMS", [])
    
    # Heuristic 1: If 0 line items were found but there are currency symbols or rows in text
    has_money = "$" in raw_text or re.search(r'\b\d+\.\d{2}\b', raw_text)
    if not line_items and has_money and len(raw_text) > 100:
        print("  [LEARNING] Triggering refinement: No items found in text containing financial data.")
        return True, 0.0, 0.0
        
    # Heuristic 2: Financial Reconciliation (Sum of items vs Total in Text)
    # Find total in text - using expanded list of common labels
    total_labels = r'(?:AMOUNT DUE|AMOUNTDUE|BALANCEDUE|BALANCE DUE|TOTAL DUE|INVOICE TOTAL|GRAND TOTAL|GRANDTOTAL|TOTAL PREMIUM|TOTALCURRENTPREMIUM|CURRENT PREMIUM)'
    total_matches = re.findall(fr'{total_labels}\s*[:$]*\s*([0-9,]+\.[0-9]{2})', raw_text.upper())
    
    # [UNUM] Mirrored Total Check
    # "TOTAL AMOUNT DUE" mirrored is "EUD TNUOMA LATOT"
    # we check for "LATOT" or "EUD LATOT" or "EUD TNUOMA"
    mirrored_labels = r'(?:LATOT|EUD LATOT|EUD TNUOMA|EUD TNUOMA LATOT|LAERNEM LATOT)'
    # Example mirrored value: "45.498$" -> needs re-reverse
    mirrored_matches = re.findall(fr'{mirrored_labels}\s*[:\$]*\s*([0-9]{2}\.[0-9,]+)\$', raw_text.upper())
    
    if not total_matches and not mirrored_matches:
        # Fallback to generic TOTAL if specific ones aren't found
        total_matches = re.findall(r'TOTAL\s*[:$]*\s*([0-9,]+\.[0-9]{2})', raw_text.upper())

    combined_targets = []
    
    # Priority Tier 1: Strong indicators of the final payable amount
    high_priority_labels = r'(?:AMOUNT DUE|AMOUNTDUE|BALANCEDUE|BALANCE DUE|INVOICED AMOUNT|TOTAL DUE)'
    hp_matches = re.findall(fr'{high_priority_labels}\s*[:$]*\s*([0-9,]+\.[0-9]{2})', raw_text.upper())
    if hp_matches:
        combined_targets.extend([float(m.replace(',', '')) for m in hp_matches])
        print(f"  [LEARNING] Detected High-Priority Total(s): {combined_targets}")

    # Priority Tier 2: Generic totals (only if Tier 1 is missing)
    if not combined_targets:
        if total_matches:
            combined_targets.extend([float(m.replace(',', '')) for m in total_matches])
    
    if mirrored_matches:
        # Reverse the mirrored numbers (e.g. "45.498" -> "894.54")
        for m in mirrored_matches:
            try:
                fixed_val = m[::-1].replace(',', '')
                combined_targets.append(float(fixed_val))
                print(f"  [LEARNING][MIRROR] Detected mirrored total: {m} -> {fixed_val}")
            except:
                pass

    if combined_targets:
        try:
            # We look for a total that is likely the 'Grand Total'.
            target_total = max(combined_targets)
            
            extracted_sum = sum(float(item.get("CURRENT_PREMIUM", 0) or 0) for item in line_items)
            
            discrepancy = abs(target_total - extracted_sum)
            if discrepancy > 0.05: # Allow 5 cents for rounding variance
                print(f"  [LEARNING] Triggering refinement: Financial mismatch (Detected Total: ${target_total:.2f}, Extracted Sum: ${extracted_sum:.2f})")
                return True, target_total, extracted_sum
        except:
            pass

    # Heuristic 3: Density Check for critical fields (MEMBERID is usually 100% present)
    if len(line_items) > 3:
        # If MEMBERID is missing on > 50% of rows, it's a red flag for invoices
        null_ids = sum(1 for item in line_items if not str(item.get("MEMBERID", "")).strip())
        if null_ids / len(line_items) > 0.5:
             print("  [LEARNING] Triggering refinement: High density of missing Member IDs.")
             return True, 0.0, 0.0

    return False, 0.0, 0.0


def save_successful_extraction(text: str, extracted_data: Dict, client) -> bool:
    """
    Analyzes a successful extraction and saves it as a new training example
    if it contains enough unique data to be useful.
    """
    line_items = extracted_data.get("LINE_ITEMS", [])
    if not line_items:
        return False

    # 1. Identify Carrier/Doc Type using LLM
    print("  [LEARNING] Identifying carrier profile for auto-training...")
    try:
        # We use a small, fast call to identify the carrier and keywords
        id_prompt = f"""
Analyze this snippet of invoice text and identify:
1. The likely CARRIER or COMPANY name (e.g. "Pet Benefits Solutions").
2. 3-4 unique KEYWORDS that appear in this document but not others (e.g. "PBS ID", "Total Pet").

TEXT SNIPPET:
{text[:2000]}

Respond ONLY with JSON:
{{
  "CARRIER_NAME": "string",
  "KEYWORDS": ["kw1", "kw2"]
}}
"""
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": id_prompt}],
            model="gpt-4o-mini", # Use mini for cost performance on meta-tasks
            temperature=0,
            response_format={ "type": "json_object" }
        )
        meta = json.loads(chat_completion.choices[0].message.content)
        carrier_name = meta.get("CARRIER_NAME", "Unknown").replace(" ", "_").lower()
        keywords = meta.get("KEYWORDS", [])
        
        if carrier_name == "unknown":
            return False

        # 2. Select 2-3 representative items as examples
        # Prioritize items with full data (MEMBERID, etc)
        best_items = [item for item in line_items if item.get("MEMBERID") and item.get("CURRENT_PREMIUM")]
        if not best_items:
            best_items = line_items
            
        examples = []
        for item in best_items[:2]:
            # Try to find the original raw text line for this item in the text
            # This is a heuristic: we look for the Firstname or MemberID
            fname = str(item.get("FIRSTNAME", ""))
            mid = str(item.get("MEMBERID", ""))
            
            raw_line = ""
            for line in text.split('\n'):
                if (fname and fname in line) or (mid and mid in line):
                    raw_line = line.strip()
                    break
            
            if raw_line:
                examples.append({
                    "RAW_TEXT": raw_line,
                    "MAPPING": item
                })

        if not examples:
            return False

        # 3. Save to training_data/
        save_data = {
            "CARRIER_KEYWORDS": keywords,
            "EXAMPLES": examples
        }
        
        os.makedirs(TRAINING_DIR, exist_ok=True)
        save_path = os.path.join(TRAINING_DIR, f"{carrier_name}_autotrained.json")
        
        # Only overwrite if the new file is "better" (more examples or fresh)
        # For simplicity, we just save it now.
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=2)
            
        print(f"  [LEARNING][SUCCESS] Auto-trained new carrier profile: {carrier_name}_autotrained.json")
        return True

    except Exception as e:
        print(f"  [LEARNING][ERROR] Auto-training failed: {e}")
        return False

def generate_refinement_prompt(extracted_data: Dict, raw_text: str, target_total: float = 0, current_sum: float = 0) -> str:
    """
    Generates a prompt specifically for fixing identified issues in the first pass.
    """
    # Detect multi-column layout (e.g. "Name Code Premium" appearing multiple times on a line)
    is_multi_column = len(re.findall(r'Name Code Premium', raw_text)) > 1
    multi_col_msg = ""
    if is_multi_column:
        multi_col_msg = """
### MULTI-COLUMN LAYOUT DETECTED:
- This document has 2 or 3 columns of members side-by-side. 
- You MUST look horizontally across the entire page to find all members.
- For each row of text, there may be multiple individuals (e.g. Left Column, Middle Column, Right Column).
- Ensure you extract every name listed in every column.
"""

    return f"""
[REFINEMENT] The previous extraction attempt had a FINANCIAL DISCREPANCY or QUALITY ISSUE.
- Expected Total Sum (from Text): ${target_total:.2f}
- Currently Extracted Sum: ${current_sum:.2f}

### REFINEMENT TASK:
1. **RE-SCAN THE DETAIL TABLE**: You missed or incorrectly extracted some members.
2. **PRIORITIZE AMOUNT DUE**: The final invoice total should match the "Amount Due" or "Balance Due" labeled in the text (${target_total:.2f}).
3. **CHECK COLUMN MAPPING**: Ensure "Total" column values are mapped to `CURRENT_PREMIUM`.
4. **MEMBER RECOVERY**: If the sum is too low, you likely missed rows. If too high, you likely captured summary rows.
{multi_col_msg}

### GUARDIAN SPECIFIC REFINEMENT (IF APPLICABLE):
- Look for the "Current Premiums" table (usually starts on Page 5 or 6).
- If you see columns like `BasicTermLife`, `Dental`, `Std`, `Vision`, you MUST extract each non-zero benefit as a SEPARATE line item.
- Do NOT just extract the "Total Premium" column. The sum of the benefit columns should equal the "Total Premium" for that row.

### REFINEMENT INSTRUCTIONS:
1. **CHECK EVERY PREMIUM**: Do NOT assume a standard rate. Look at EACH member's line in the raw text. 
2. **ZERO HALLUCINATION**: If the text says one number, return that EXACT number. Never invent a number.
3. **COMPLETE CAPTURE**: Ensure EVERY name listed in the detail table (in ALL columns) is captured.

TEXT TO RE-PROCESS:
{raw_text}
"""
