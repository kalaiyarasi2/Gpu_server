from dynamic_extraction_prototype import DynamicExtractionManager
import json

def run_demo():
    # 1. Setup
    # In a real scenario, this would have an OpenAI client
    manager = DynamicExtractionManager(openai_client=None)

    # 2. Add Requirements (Phase 1)
    print("--- Phase 1: Requirements ---")
    manager.add_requirement("Transaction Date", "The date the transaction was posted or occurred.")
    manager.add_requirement("Merchant/Description", "The name of the vendor or description of the activity.")
    manager.add_requirement("Amount", "The dollar amount of the transaction.")
    manager.add_requirement("Check Number", "The number of the check if applicable.", required=False)
    
    for req in manager.requirements:
        print(f"Added Requirement: {req.field_name}")

    # 3. Structural Discovery (Phase 2)
    print("\n--- Phase 2: Structural Discovery (Mocking LLM Response) ---")
    # Mocking a Zions-style split-column text for the demo
    sample_text_page1 = """
    ZIONS BANK
    Statement for Period: 01/01/2024 to 01/31/2024
    
    DEPOSITS/CREDITS
    Date   Date    Amount   Description
    01/02  01/02   1,500.00 PAYROLL DEPOSIT
    01/15  01/15     200.00 ZELLE TRANSFER FROM JM
    
    CHARGES/DEBITS
    Date   Date    Amount   -   Description
    01/05  01/05     45.20  -   STARBUCKS #123
    01/06  01/06    120.00  -   Check No: 1502
    """
    
    discovery_result = {
        "archetype": "split-column",
        "sections": [
            {
                "name": "Deposits/Credits",
                "start_marker": "DEPOSITS/CREDITS",
                "end_marker": "CHARGES/DEBITS",
                "columns": ["Post Date", "Effective Date", "Amount", "Description"],
                "has_checks": False
            },
            {
                "name": "Charges/Debits",
                "start_marker": "CHARGES/DEBITS",
                "end_marker": "CHECKS PROCESSED",
                "columns": ["Post Date", "Effective Date", "Amount", "Separator", "Description"],
                "has_checks": True
            }
        ],
        "date_format": "MM/DD",
        "currency_notes": "Amounts in Charges/Debits are outflows; ' - ' separator used before description."
    }
    manager.discovered_structure = discovery_result
    print("Discovery complete. Layout identified as 'split-column'.")

    # 4. Dynamic Schema Identification (Phase 3)
    print("\n--- Phase 3: Dynamic Schema Identifier ---")
    # Mocking the alignment logic
    schema = {
        "mappings": [
            {
                "field": "Transaction Date",
                "source_section": "Multiple",
                "extraction_logic": "Map to column 1 ('Post Date') in both Deposits and Charges sections."
            },
            {
                "field": "Merchant/Description",
                "source_section": "Multiple",
                "extraction_logic": "Map to 'Description' (column 4 in Deposits, column 5 in Charges after the ' - ')."
            },
            {
                "field": "Check Number",
                "source_section": "Charges/Debits",
                "extraction_logic": "Regex search for 'Check No: (\d+)' within the Description column."
            }
        ]
    }
    manager.dynamic_schema = schema
    print("Schema Generated Successfully:")
    print(json.dumps(schema, indent=2))

if __name__ == "__main__":
    run_demo()
