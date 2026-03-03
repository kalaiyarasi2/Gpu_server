import re

def detect_reversed_text(text: str) -> bool:
    """
    Detect if the text appears to be reversed (mirrored).
    """
    # Use very high-confidence mirrored OCR patterns
    reversed_patterns = [
        "sdioani", "s0iovui", "adiovui", "eciovni", "eciovnu", # INVOICE
        "esos", "szoz", "scoz", "ezos",  # 2025/2026
        "voitaat2", "240ivaa2", "evitatneserpeR",        # ADMINISTRATION / SERVICES / Representative
        "sssal9", "anig", "auie", "anigruoc", "anamuh",   # CROSS / BLUE / Insurance / Humana
        "fih2@", "muimerp", "tnemetats", "gnillib", "rebmun", "etad", "egap", # MEMBERSHIP / UNUM keywords
        "ytnuoc"                         # COUNTRY
    ]
    
    # Remove all whitespace and common punctuation for robust matching
    clean_text = re.sub(r'[^a-zA-Z0-9]', '', str(text)).lower()
    
    match_count = 0
    for pattern in reversed_patterns:
        if pattern in clean_text:
            print(f"Match found: {pattern}")
            match_count += 1
            
    return match_count >= 1

# Snippet from Unum raw text
unum_text = """
3: tnemetatS muimerP
4: CLL ,NROBRAED EGARAG :emaN gnilliB
5: 0 100-5362890 :rebmuN gnilliB
6: 6202/1/3 :etaD euD
7: 6202/31/2 :etaD tnemetatS
"""

print(f"Detection result: {detect_reversed_text(unum_text)}")
