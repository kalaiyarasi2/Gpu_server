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

Return JSON structure:
{{
  "archetype": "standard | split-column | hybrid",
  "sections": [
    {{
      "name": "string",
      "start_marker": "string regex",
      "end_marker": "string regex",
      "columns": ["col1", "col2"],
      "has_checks": boolean
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
Define logic for extracting these fields (e.g., 'Extract from the "Description" column', 'Look for 5-digit numbers' etc.).

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
