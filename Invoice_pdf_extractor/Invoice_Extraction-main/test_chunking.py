import re

def test_regex():
    text = """
[[PAGE_1]]
Some data for page 1.
Multiple lines.

[[PAGE_2]]
More data for page 2.

[[PAGE_3]]
Finally page 3.
"""
    
    page_marker_pattern = r'\[\s*\[\s*PAGE_\d+\s*\]\s*\]'
    page_markers = re.findall(page_marker_pattern, text)
    pages = re.split(page_marker_pattern, text)
    
    if pages and not pages[0].strip():
        pages.pop(0)
    
    pages = [p.strip() for p in pages]
    
    print(f"Markers found: {len(page_markers)}")
    print(f"Pages found: {len(pages)}")
    
    for i, page in enumerate(pages):
        print(f"Page {i+1} content length: {len(page)}")
        print(f"Page {i+1} preview: {page[:30]}...")

    assert len(page_markers) == 3
    assert len(pages) == 3
    assert "page 1" in pages[0].lower()
    assert "page 2" in pages[1].lower()
    assert "page 3" in pages[2].lower()

if __name__ == "__main__":
    try:
        test_regex()
        print("\n[OK] Page splitter regex test passed!")
    except Exception as e:
        print(f"\n[FAIL] Page splitter regex test failed: {e}")
        import traceback
        traceback.print_exc()
