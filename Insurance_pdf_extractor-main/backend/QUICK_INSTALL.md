# Quick Install Guide - OCRmyPDF Enhancement

## Current Status:
✅ Tesseract OCR: **INSTALLED** (v5.5.0)  
❌ Ghostscript: **MISSING**  
❌ OCRmyPDF: **MISSING**

## Install Missing Dependencies

### Step 1: Install Ghostscript (2 minutes)

**Download:**
1. Visit: https://www.ghostscript.com/releases/gsdnld.html
2. Download: **Ghostscript 10.04.0 for Windows (64 bit)**
3. Run installer
4. Use default settings

**Or use Chocolatey (if installed):**
```powershell
choco install ghostscript
```

### Step 2: Install OCRmyPDF (1 minute)

```bash
pip install ocrmypdf
```

### Step 3: Verify Installation

```bash
ocrmypdf --version
```

## Test the Enhancement

```bash
cd C:\Users\Intern\gpu\Gpu_server\Insurance_pdf_extractor-main\backend
python ocrmypdf_enhancer.py
```

This will process your problematic PDF:
- Input: `processed_Loss Runs 1-7.pdf`
- Output: `extracted_text_enhanced.txt`

## Expected Results

**Before (current):**
- Garbled text
- Missing data
- ~500 characters of usable text

**After (with OCRmyPDF):**
- Clean, complete text
- All table data visible
- 5000+ characters of accurate text

---

**Estimated Time:** 5 minutes to install + ~2 minutes per PDF to process
