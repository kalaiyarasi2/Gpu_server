from backend.statement_extractor import StatementExtractor
import json

def test_sorting():
    extractor = StatementExtractor()
    
    # Test Deposits (Chronological MM/DD)
    deposits = [
        {"date": "01/15", "amount": 100.0, "description": "DEP 2"},
        {"date": "01/02", "amount": 200.0, "description": "DEP 1"},
        {"date": "01/20", "amount": 50.0, "description": "DEP 3"},
        {"date": "01/02", "amount": 200.0, "description": "DEP 1"}, # Duplicate
    ]
    
    final_dep = extractor._finalize_deposits(deposits)
    print("Final Deposits (Sorted & Deduped):")
    for d in final_dep: print(f"  {d}")
    
    # Test Debits (Check No primarily, then Date)
    debits = [
        {"check_no": "6232106", "amount": 500.0, "date": "01/10"},
        {"check_no": "6232002", "amount": 1500.0, "date": "01/05"},
        {"check_no": "None", "amount": 20.0, "date": "01/12"},
        {"check_no": None, "amount": 45.0, "date": "01/02"},
        {"check_no": "6232002", "amount": 1500.0, "date": "01/05"}, # Duplicate
        {"check_no": "6232057", "amount": 300.0, "date": "01/08"},
    ]
    
    final_deb = extractor._finalize_debits(debits)
    print("\nFinal Debits (Sorted & Deduped):")
    for d in final_deb: print(f"  {d}")

if __name__ == "__main__":
    test_sorting()
