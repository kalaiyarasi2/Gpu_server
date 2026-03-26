import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class ExtractionRequirement:
    field_name: str
    description: str
    is_required: bool = True
    data_type: str = "string"  # string, number, date

class DynamicExtractionManager:
    """
    Manages the lifecycle of dynamic extraction:
    1. Define Requirements
    2. Analyze Document Structure
    3. Generate Schema
    4. Execute extraction
    """
    
    def __init__(self, openai_client):
        self.client = openai_client
        self.requirements: List[ExtractionRequirement] = []
        self.discovered_structure: Optional[Dict[str, Any]] = None
        self.dynamic_schema: Optional[Dict[str, Any]] = None

    def add_requirement(self, field: str, description: str, required: bool = True, type: str = "string"):
        self.requirements.append(ExtractionRequirement(field, description, required, type))

    def analyze_document_flow(self, sample_text: str) -> Dict[str, Any]:
        """
        Phase 2: Structural Discovery
        Analyzes 2-4 pages of text to understand document layout.
        """
        prompt = f"""You are a Document Engineering Expert. Analyze these 2-4 pages of a bank statement and define its structural archetype.

TEXT CONTENT:
---
{sample_text}
---

TASK:
1. Identify major sections (e.g., 'Summary of Accounts', 'Deposits/Credits', 'Checks Transacted', 'Fees').
2. Identify the 'Table Strategy':
   - 'standard': Single vertical table with columns.
   - 'multi-column-split': Parallel tables (like Zions Bank where deposits and debits are side-by-side or interleaved in columns).
   - 'interleaved': Transaction flow with mixed types.
3. Identify column headers for each section.
4. Detect formatting quirks (e.g., debits in parentheses, check numbers with leading zeros, dates without years).
5. IMPORTANT: Determine the 'Sign Convention' (Debits vs Credits):
   - Identify if the document uses separate columns for 'Debits' and 'Credits' (or 'Withdrawals' and 'Deposits').
   - If it's a single column, identify the 'Sign Convention' (e.g., debits are in parentheses, or debits are positive and credits are negative).
   - Trace a known transaction (like a 'DEPOSIT' or 'CHECK') to see which column it appears in or what sign it has.
   - State clearly: 'Separate columns for Debits/Credits' or 'Single Amount column (Debits positive)'.
6. LAYOUT ANALYSIS: Identify if Date, Amount, and Description are in a single row or split into separate vertical blocks (column-split).
7. DATE AND BALANCE DIFFERENTIATION:
   - Identify if there are multiple date columns (e.g., 'Trans Date' vs 'Post Date'). Note which one is the actual transaction date.
   - Identify if there is a 'Balance' or 'Running Balance' column. This MUST be distinguished from the 'Amount' column. This is critical because the balance column is often right next to the debit/credit columns and can be easily confused.
   - Determine the 'Balance Side': State whether the balance is on the far right, far left, or middle.
8. SUMMARY vs TRANSACTIONS: Be extremely careful with sections that contain summary tables (e.g., 'Balance Summary', 'Account Summary Information'). These often list totals for 'ACH Debits' or 'Deposits' which are NOT individual transactions. Ensure your `start_marker` for transactions skips these summary blocks if they appear at the very start of a page.
9. MULTI-ACCOUNT DETECTION: If the document contains multiple accounts (e.g., 'Checking 0123' and 'Checking 4567'), identify if each account has its own transaction section. If so, provide a generic `start_marker` that captures ALL of them (or a list of specific ones).
10. CONTINUANCE: Detect markers like 'Continued' or 'Page X of Y' that appear near section headers. Ensure `start_marker` and `end_marker` are robust enough to handle these repeating headers across pages without cutting off data.

Return JSON structure:
{{
  "archetype": "standard | split-column | hybrid",
  "balance_column_identified": true | false,
  "balance_column_position": "far-right | near-amount | none",
  "sections": [
    {{
      "name": "string",
      "start_marker": "string regex (prefer specific markers like '^\\\\s*Transactions\\\\b' to avoid partial matches in sentences)",
      "end_marker": "string regex (prefer markers like '^\\\\s*Fees\\\\b' or specific footer labels)",
      "columns": ["col1", "col2"],
      "has_checks": boolean,
      "beginning_balance": "float or null (if found in a summary table above this section)"
    }}
  ],
  "date_format": "MM/DD | DD/MM | ...",
  "currency_notes": "How credits/debits are distinguished"
}}
"""
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        self.discovered_structure = json.loads(response.choices[0].message.content)
        return self.discovered_structure

    def generate_dynamic_schema(self) -> Dict[str, Any]:
        """
        Phase 3: Dynamic Schema Identification
        Aligns requirements with discovered structure.
        """
        if not self.discovered_structure or not self.requirements:
            raise ValueError("Missing discovery data or requirements")

        req_json = json.dumps([r.__dict__ for r in self.requirements], indent=2)
        struct_json = json.dumps(self.discovered_structure, indent=2)

        prompt = f"""Based on the Document Structure and the Extraction Requirements, create a Dynamic Extraction Schema.

DOCUMENT STRUCTURE:
{struct_json}

REQUIREMENTS:
{req_json}

TASK:
Map each requirement to a specific section and column set identified in the structure. 
Define logic for extracting these fields (e.g., 'Extract from the "Description" column', 'Extract from the "Debits" column' etc.).

IMPORTANT: 
- If the DOCUMENT STRUCTURE shows separate columns for 'Debits' and 'Credits' (or similar), map 'Withdrawal Amount' to the Debits column and 'Deposit Amount' to the Credits column. 
- If there is only one 'Amount' column, use the 'currency_notes' to define how to distinguish between withdrawals and deposits.

SPECIAL INSTRUCTION FOR 'Check Number':
- Look for fields labeled 'Check', 'CK', or numbers in a 'Check' column.
- Check numbers are typically 3-6 digits long.
- CAUTION: Do NOT confuse Check Numbers with long Bill Payment IDs or Reference Numbers (often 10+ digits).
- If a number is prefixed with '#' but is very long (e.g. 10 or 12 digits), it is likely an Electronic/Bill Pay ID, NOT a check number.

Return JSON:
{{
  "mappings": [
    {{
      "field": "field_name",
      "source_section": "section_name",
      "extraction_logic": "description of logic",
      "confidence_boosters": ["key words to look for"]
    }}
  ]
}}
"""
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        self.dynamic_schema = json.loads(response.choices[0].message.content)
        return self.dynamic_schema
    def execute_extraction(self, full_text: str) -> List[Dict[str, Any]]:
        """
        Phase 4: Dynamic Extraction Execution
        Uses the schema to guide row extraction from the text.
        """
        if not self.dynamic_schema:
            raise ValueError("No dynamic schema generated")

        all_transactions = []
        global_index = 0
        
        # Strategy: Segment text by discovered sections and extract rows
        sections = self.discovered_structure.get("sections", [])
        for section in sections:
            # We focus on sections that likely contain transactions
            section_name = section.get("name", "")
            if any(k in section_name.lower() for k in ["transaction", "deposit", "credit", "debit", "withdrawal", "charge", "check"]):
                
                # Extract the raw text block for this section
                start_re = section.get("start_marker", "")
                end_re = section.get("end_marker", "")
                
                import re
                try:
                    # Multi-page/multi-block support: find all occurrences of the section
                    last_pos = 0
                    while True:
                        search_text = full_text[last_pos:]
                        start_match = re.search(start_re, search_text, flags=re.IGNORECASE | re.MULTILINE)
                        if not start_match:
                            break
                        
                        abs_start_pos = last_pos + start_match.end()
                        abs_end_pos = len(full_text)
                        
                        # Optimization: The end of this section block should NOT contain the START of the same section 
                        # for a subsequent page (which results in overlaps). 
                        # We search for the end marker, but if it's too far or missing, we cap it at the next start marker.
                        
                        end_match = re.search(end_re, full_text[abs_start_pos:], flags=re.IGNORECASE | re.MULTILINE)
                        
                        # Also look for the next occurrence of the same start marker to avoid overlap
                        next_start_match = re.search(start_re, full_text[abs_start_pos:], flags=re.IGNORECASE | re.MULTILINE)
                        
                        if end_match:
                            abs_end_pos = abs_start_pos + end_match.start()
                            # If we found another start marker BEFORE the end marker, check if it's a "Continued" header or a new section
                            if next_start_match and next_start_match.start() < end_match.start():
                                # Check if it's the SAME section repeating (continued)
                                # If so, we DON'T stop here, we should include the text until the REAL end_marker or a DIFFERENT section
                                # However, for simplicity in LLM processing, we often prefer smaller chunks.
                                # But if we stop, we might lose the row that was split.
                                # Fix: Stop only if it's a DIFFERENT section or if we've reached a logical page break that we can resume from.
                                abs_end_pos = abs_start_pos + next_start_match.start()
                            
                            next_search_pos = abs_start_pos + (next_start_match.start() if next_start_match else end_match.end())
                        elif next_start_match:
                            # No end marker found, but next start marker exists. 
                            abs_end_pos = abs_start_pos + next_start_match.start()
                            next_search_pos = abs_start_pos + next_start_match.start()
                        else:
                            abs_end_pos = len(full_text)
                            next_search_pos = len(full_text)
                        
                        section_text = full_text[abs_start_pos:abs_end_pos].strip()
                        if section_text:
                            print(f"   ➤ Extracting from {section_name} block ({len(section_text)} chars)...")
                            # Use LLM to extract rows from this specific block using the schema
                            section_mapping = [m for m in self.dynamic_schema["mappings"] if m["source_section"] == section_name]
                            
                            prompt = f"""Extract transactions from the following bank statement section text.
                            
SECTION NAME: {section_name}
SECTION COLUMNS: {section.get("columns", [])}
BALANCE COLUMN POSITION: {self.discovered_structure.get("balance_column_position", "unknown")}
SCHEMA MAPPINGS: {json.dumps(section_mapping, indent=2)}

SECTION TEXT:
---
{section_text}
---

TASK:
Return each transaction row as a JSON object in a list.
- Use these field names: "date", "description", "amount", "check_no" (if present), "running_balance".

CRITICAL - SPLIT-COLUMN LAYOUT INSTRUCTIONS:
If the text appears in BLOCKS (a block of Trans. Dates, then a block of Posting Dates, then a block of Amounts/Balances, then a block of Descriptions), you MUST reconstruct it:
1. Identify the Nth date → pair it with the Nth amount in the amount block → pair it with the Nth description.
2. DOLLARS AND CENTS SPLIT (VERY COMMON): In some layouts, the dollars and cents are split by MANY lines. e.g., '2,231.' appears on line 369, but the cents '84' appear on line 427. You MUST scan the surrounding 'digit blocks' and merge them. '2,231.' + '84' = 2231.84.
3. BALANCE COLUMN RECONSTRUCTION: The Balance column values are often on the far right. If you see two different numeric sequences in the amount block for a single row, the first is likely the amount and the second is the balance.
4. The 'Withdrawal/Debit' and 'Deposit/Credit' columns may merge with the 'Balance' column in OCR output. Parse carefully.

STANDARDIZATION RULES:
- amount: numeric (ONLY the transaction amount - the debit or credit value).
  - SIGN: {self.discovered_structure.get("currency_notes", "Standard")}. Deposits/Credits = Positive (+), Withdrawals/Debits = Negative (-).
  - CRITICAL - BALANCE SHIELD: The column on the FAR RIGHT is the **Running Balance**. You are STRICTLY PROHIBITED from extracting the Running Balance value into the `amount` field.
  - If a row has `95.96` and `1,582.10`, where `1,582.10` is on the far right: `95.96` is the amount, and `1,582.10` is the running balance.
- running_balance: numeric (MANDATORY). This is the account total AFTER this transaction (the far-right column).
  - CRITICAL: You MUST provide this value for every single transaction row. It is the definitive anchor for our mathematical verification engine.
  - RULE: If a line or row has multiple numbers, the one on the **FAR RIGHT** (the last one horizontally) is ALWAYS the `running_balance`.
  - NO MISMATCH: Mismatching the transaction amount with the running balance is a CRITICAL FAILURE. Accuracy is the highest priority.
  - RECONSTRUCTION: In split-column layouts, the `running_balance` might appear as a decimal fragment (e.g., '.84') in a block of numbers 30-50 lines below the description. You MUST pair the Nth number in that fragment block with the Nth transaction.
  - If you are absolutely uncertain, pick the number that seems most like a total (usually the largest or last). Never leave it null unless the entire column is blank.
- date: MM/DD
- description: full description string
- check_no: physical check number only (3-6 digits).

EXCLUSIONS:
- IGNORE rows with: "Summary", "Total", "Beginning Balance", "Ending Balance", "ACH Debits", "Deposits and Other Credits"
- Do NOT invent transactions. Only extract what EXPLICITLY appears in the section text.

Return JSON:
{{
  "transactions": [
    {{ "date": "...", "description": "...", "amount": ..., "check_no": ..., "running_balance": ... }}
  ]
}}
"""
                            response = self.client.chat.completions.create(
                                model="gpt-4o",
                                messages=[{"role": "user", "content": prompt}],
                                response_format={"type": "json_object"},
                                temperature=0.0
                            )
                             
                            rows = json.loads(response.choices[0].message.content).get("transactions", [])
                            for r in rows:
                                r["original_index"] = global_index
                                global_index += 1
                                all_transactions.append(r)
                        
                        last_pos = next_search_pos
                        if last_pos >= len(full_text):
                            break
                    
                except Exception as e:
                    print(f"   ⚠️ Error extracting section {section_name}: {e}")
                    
        return all_transactions
