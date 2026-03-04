import sys
import os

# Add the backend directory to sys.path
backend_dir = r"C:\Users\INTERN\main_project\Main--main\work_compenstaion\backend"
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from work_compensation import parse_p3_gio_from_text
    print("✓ Successfully imported parse_p3_gio_from_text")
    
    test_text = """
    Random text before.
    P3_GIO_Q1: Y
    P3_GIO_Q2: Yes
    P3_GIO_Q3: N
    P3_GIO_Q4: No
    P3_GIO_Q5: yes
    P3_GIO_Q24: Y
    """
    
    result = parse_p3_gio_from_text(test_text)
    print(f"Result: {result}")
    
    expected = {"q1": "Y", "q2": "Y", "q3": "N", "q4": "N", "q5": "Y", "q24": "Y"}
    for k, v in expected.items():
        if result.get(k) != v:
            print(f"✗ Validation failed for {k}: expected {v}, got {result.get(k)}")
            sys.exit(1)
            
    print("✓ Validation successful!")
    
    # Test initialization check (same as unified_router.py)
    try:
        from chunked_extractor import ChunkedInsuranceExtractor
        print("✓ Successfully imported ChunkedInsuranceExtractor from work_comp backend")
    except Exception as e:
        print(f"✗ Failed to import ChunkedInsuranceExtractor: {e}")
        sys.exit(1)

except Exception as e:
    print(f"✗ Critical error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
