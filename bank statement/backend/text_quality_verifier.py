import re
from typing import Dict, List, Tuple

class BankTextQualityVerifier:
    """
    Analyzes extracted text quality to detect noise, garbage characters,
    or incomplete extractions.
    """
    
    def __init__(self, thresholds: Dict = None):
        self.thresholds = thresholds or {
            'min_chars_per_page': 100,     # Bank statements can be very sparse on some pages
            'max_cid_count': 15,           # Lowered from 30 to catch earlier CID issues
            'max_noise_ratio': 0.18,       # Lowered from 0.25 to catch earlier noise
            'min_keywords': 4,             # Increased from 3
            'max_noisy_line_ratio': 0.25   # Lowered from 0.40 to be more sensitive to garbled lines
        }
        
        self.critical_keywords = [
            'account', 'statement', 'balance', 'date', 'amount', 'transaction', 
            'deposit', 'withdrawal', 'credit', 'debit', 'summary', 'check',
            'posting', 'effective', 'description'
        ]

    def analyze_quality(self, text: str, num_pages: int = 1) -> Dict:
        """
        Performs comprehensive quality analysis on extracted text.
        """
        if not text:
            return {
                'is_acceptable': False,
                'reason': 'Empty text',
                'scores': {'length': 0}
            }
            
        # 1. Check CID codes (digital garbage indicator)
        cid_count = text.count("(cid:")
        
        # 2. Check Noise Ratio (non-alphanumeric vs total)
        # We allow spaces and common punctuation ($, . , / -)
        clean_text = re.sub(r'[\s\$\.\,\/\-\:]', '', text)
        alphanumeric = re.sub(r'[^a-zA-Z0-9]', '', clean_text)
        
        if len(clean_text) > 0:
            noise_ratio = 1.0 - (len(alphanumeric) / len(clean_text))
        else:
            noise_ratio = 1.0
            
        # 3. Check Completeness (average chars per page)
        chars_per_page = len(text) / max(1, num_pages)
        
        # 4. Keyword presence
        found_keywords = [k for k in self.critical_keywords if k.lower() in text.lower()]
        
        # 5. Gibberish Detection (Vowel ratio and nonsense word check)
        # Most English words have at least 20-30% vowels.
        words = re.findall(r'[a-zA-Z]{3,}', text)
        vowel_count = len(re.findall(r'[aeiouAEIOU]', text))
        alpha_count = len(re.findall(r'[a-zA-Z]', text))
        vowel_ratio = vowel_count / max(1, alpha_count)
        
        # Nonsense words (very long strings with no vowels or high consonant ratio)
        nonsense_words = []
        for word in words:
            if len(word) > 8:
                w_vowels = len(re.findall(r'[aeiouAEIOU]', word))
                if w_vowels / len(word) < 0.15: # Less than 15% vowels in a long word
                    nonsense_words.append(word)

        # 6. Line-level noise check
        lines = text.split('\n')
        noisy_lines = 0
        total_valid_lines = 0
        
        for line in lines:
            line = line.strip()
            if len(line) < 5: continue
            total_valid_lines += 1
            
            # Check if line is mostly symbols or repetitive chars
            l_alpha = len(re.findall(r'[a-zA-Z0-9]', line))
            l_total = len(line)
            if l_alpha / l_total < 0.5: # 50% of line is symbols
                noisy_lines += 1
            elif len(re.findall(r'[\|_]{3,}', line)) > 0: # Repetitive table separators
                noisy_lines += 1
                
        noisy_line_ratio = noisy_lines / max(1, total_valid_lines)

        # Determine acceptability
        reasons = []
        if cid_count > self.thresholds['max_cid_count']:
            reasons.append(f"High CID count ({cid_count})")
        
        if noise_ratio > self.thresholds['max_noise_ratio']:
            reasons.append(f"High noise ratio ({noise_ratio:.2f})")
            
        if chars_per_page < self.thresholds['min_chars_per_page']:
            reasons.append(f"Sparse text content ({int(chars_per_page)} chars/page)")
            
        if len(found_keywords) < self.thresholds.get('min_keywords', 4):
            reasons.append(f"Missing core keywords (found {len(found_keywords)})")
            
        if alpha_count > 100 and vowel_ratio < 0.22:
            reasons.append(f"Suspiciously low vowel ratio ({vowel_ratio:.2f}) - likely gibberish")
            
        if len(nonsense_words) > 3:
            reasons.append(f"Detected {len(nonsense_words)} nonsense words")
            
        if noisy_line_ratio > self.thresholds.get('max_noisy_line_ratio', 0.15):
            reasons.append(f"High ratio of noisy lines ({noisy_line_ratio:.2f})")
            
        is_acceptable = len(reasons) == 0
        
        return {
            'is_acceptable': is_acceptable,
            'reason': "; ".join(reasons) if not is_acceptable else "Acceptable",
            'metrics': {
                'cid_count': cid_count,
                'noise_ratio': round(noise_ratio, 3),
                'vowel_ratio': round(vowel_ratio, 3),
                'chars_per_page': int(chars_per_page),
                'keyword_count': len(found_keywords),
                'nonsense_word_count': len(nonsense_words),
                'noisy_line_ratio': round(noisy_line_ratio, 3)
            }
        }

    def detect_missing_columns(self, text: str) -> bool:
        """
        Detects if an 'Amount' column exists in headers but is missing in rows.
        Crucial for catching right-clipped OCR.
        """
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return False

        # 1. Header Detection
        has_amount_header = False
        amount_header_line_idx = -1
        # Look for headers in first 25 lines (bank statements can have long headers)
        for i, line in enumerate(lines[:25]): 
            if re.search(r'\b(Amount|Total|Balance|Charge|Credit|Debit)\b', line, re.IGNORECASE):
                has_amount_header = True
                amount_header_line_idx = i
                break
        
        if not has_amount_header:
            return False

        # 2. Row Analysis (looking for rows with dates but no amounts)
        date_rows = 0
        rows_with_amounts = 0
        
        # Start checking after the header
        for line in lines[amount_header_line_idx + 1:]:
            # Quick check for date-like pattern (MM/DD or MM-DD or Month DD)
            has_date = bool(re.search(r'\b\d{1,2}[/\-]\d{1,2}\b', line)) or \
                       bool(re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}\b', line, re.IGNORECASE))
            
            if has_date:
                date_rows += 1
                # Check for currency/decimal pattern: 1,234.56 or 12.34
                if re.search(r'\b\d[\d,]*\.\d{2}\b', line):
                    rows_with_amounts += 1
        
        # 3. Thresholding
        # Be extremely sensitive. 
        if date_rows >= 2:
            ratio = rows_with_amounts / date_rows
            # If at least 15% of rows are missing amounts, or if we have at least 1 missing amount in a short list
            missing_count = date_rows - rows_with_amounts
            if ratio < 0.85 or missing_count >= 1:
                print(f"   ⚠️ Quality Alert: Structural Gap detected (Dates: {date_rows}, With Amounts: {rows_with_amounts}, Missing: {missing_count})")
                return True
        
        return False

    def should_fallback_to_vision(self, text: str, num_pages: int = 1) -> Tuple[bool, str]:
        """
        Determines if text extraction is poor enough to require Vision OCR fallback.
        """
        analysis = self.analyze_quality(text, num_pages)
        if not analysis['is_acceptable']:
            return True, analysis['reason']
        return False, ""

    def quality_score(self, text: str, num_pages: int = 1) -> float:
        """
        Returns a numeric quality score from 0.0 (unusable) to 1.0 (perfect).
        Useful for comparing quality before and after a fallback attempt.
        """
        if not text or not text.strip():
            return 0.0
        analysis = self.analyze_quality(text, num_pages)
        metrics = analysis.get('metrics', {})
        
        penalties = 0.0
        # CID codes: heavy penalty
        if metrics.get('cid_count', 0) > self.thresholds.get('max_cid_count', 30):
            penalties += 0.4
        # Noise ratio
        nr = metrics.get('noise_ratio', 0)
        if nr > self.thresholds.get('max_noise_ratio', 0.20):
            penalties += min(0.3, nr)
        # Vowel ratio (gibberish)
        vr = metrics.get('vowel_ratio', 0.3)
        if vr < 0.22:
            penalties += 0.2
        # Noisy lines
        nlr = metrics.get('noisy_line_ratio', 0)
        if nlr > self.thresholds.get('max_noisy_line_ratio', 0.15):
            penalties += min(0.2, nlr)
        # Keywords
        kw = metrics.get('keyword_count', 0)
        kw_penalty = max(0, (self.thresholds.get('min_keywords', 4) - kw)) * 0.05
        penalties += kw_penalty
        
        return max(0.0, round(1.0 - penalties, 3))

    def fallback_recommendation(self, text: str, num_pages: int = 1) -> str:
        """
        Returns a recommendation string:
          - 'ok'          : text quality is acceptable, proceed normally
          - 'dpi_fallback': try re-extracting at DPI 300 using Vision
          - 'full_vision' : text is severely degraded, use full Vision pipeline immediately
        """
        analysis = self.analyze_quality(text, num_pages)
        if analysis['is_acceptable']:
            return 'ok'
        
        score = self.quality_score(text, num_pages)
        # If score is very low (< 0.4), go straight to full Vision
        if score < 0.4:
            return 'full_vision'
        # Moderate quality issues: try DPI 300 Vision first
        return 'dpi_fallback'

    def analyze_pages(self, pages_text: Dict[int, str]) -> Dict[int, Dict]:
        """
        Per-page quality analysis.

        Returns a mapping:
            {
              page_number: {
                "analysis": <analyze_quality result>,
                "score": <0.0-1.0>,
                "recommendation": "ok" | "dpi_fallback" | "full_vision"
              },
              ...
            }
        """
        results: Dict[int, Dict] = {}
        for page_num, text in pages_text.items():
            analysis = self.analyze_quality(text, num_pages=1)
            score = self.quality_score(text, num_pages=1)
            recommendation = self.fallback_recommendation(text, num_pages=1)
            results[page_num] = {
                "analysis": analysis,
                "score": score,
                "recommendation": recommendation,
            }
        return results

    def page_quality(self, page_text: str) -> Dict:
        """
        Convenience helper: run quality analysis for a single page.
        """
        analysis = self.analyze_quality(page_text, num_pages=1)
        score = self.quality_score(page_text, num_pages=1)
        recommendation = self.fallback_recommendation(page_text, num_pages=1)
        return {
            "analysis": analysis,
            "score": score,
            "recommendation": recommendation,
        }
