# Project Overview: Unified Intelligent PDF Extraction Platform

This document provides a comprehensive map of the system architecture, extraction workflows, and advanced features of the PDF Extractor project, including the recent performance and GPU optimizations.

---

## 1. Core Architecture & Component Map

The project is structured as a "Waterfall Pipeline," where documents are routed through increasingly sophisticated extraction layers based on their type and quality.

### Primary Directories:
*   `Insurance_pdf_extractor-main/`: The core engine for insurance-specific data extraction.
*   `Unified_PDF_Platform/`: The entry point and intelligent router that handles multiple document types.
*   `Email_pipeline/`: Automated ingestion from Gmail/Outlook for hands-free processing.
*   `shared_utils/`: Common logic for data validation, formatting, and mathematical verification.

---

## 2. The Extraction "Waterfall" (Step-by-Step Flow)

Every document follows this optimized execution path:

### Step 1: Intelligent Detection (`pdf_detector.py`)
- The system automatically determines if a PDF is **Digital** (text-based) or **Scanned** (image-based).
- Detects page count, orientation, and potential text corruption (reversed encoding).

### Step 2: Adaptive Extraction (Hybrid Mode)
- **Digital PDFs:** Uses `pdf_plumber.py` with parallel page processing.
    - **Hybrid Fallback:** Runs `pdfplumber` (for layout) and `PyMuPDF` (for robust text) in parallel.
    - **Smart Recovery:** If one engine misses a claim ID found by the other, it merges the data automatically.
- **Scanned PDFs:** Uses the OCR Waterfall:
    1. **Rostaing-OCR (GPU Accelerated):** First priority. Preserves tables and columns perfectly using local GPU.
    2. **Tesseract (CPU Parallel):** Used as a secondary fallback with multi-DPI scanning.
    3. **Vision OCR:** Final fallback using high-cost AI vision models only if local OCR fails quality checks.

### Step 3: Pre-Processing & Quality Check (`text_quality_verifier.py`)
- **Auto-Rotation:** Pages are checked for orientation and deskewed in parallel.
- **Garbage Detection:** Checks for CID errors, noise ratios, and "gibberish" text.
- **Reversal Correction:** Automatically fixes PDFs where text is encoded backwards.

### Step 4: Intelligent Chunking (`chunked_extractor.py`)
- **Boundary Detection:** Uses parallel AI calls to find where one policy ends and another begins.
- **Context Preservation:** Overlaps chunks to ensure claim data straddling a page break is never lost.
- **Smart Format Validation:** Analyzes the document layout once and verifies it for subsequent chunks, saving 90% of layout-analysis time.

### Step 5: AI-Driven Extraction (`insurance_extractor.py`)
- Uses GPT-4o with highly specialized insurance prompts.
- **Multi-Stage Extraction:**
    1. **Discovery:** Finds all valid Claim IDs first.
    2. **Targeted Extraction:** Pulls financial data (Paid, Reserve, Incurred) for each ID.
    3. **Math Verification:** Cross-checks "Paid + Reserve = Incurred" to ensure data integrity.

---

## 3. Key Advanced Features

### 🚀 Performance Optimizations (New)
*   **Parallel Page Processing:** Extraction and OCR occur on multiple pages simultaneously.
*   **GPU Hardware Scaling:** Automatically detects NVIDIA/Intel GPUs and scales worker counts based on VRAM (8GB, 16GB, 24GB+).
*   **AI Scan Concurrency:** Policy boundaries and format validations are sent to the AI in parallel batches.

### 🛡️ Accuracy & Data Integrity
*   **Mathematical Audit:** The system validates that subtotals match the grand totals in the extracted data.
*   **Deduplication:** Handles documents with duplicate claim numbers or overlapping policy years.
*   **Carrier Detection:** Automatically identifies the insurance carrier and applies specific rules (e.g., Travelers vs. Liberty Mutual formatting).

### ⚙️ Scalability & Infrastructure
*   **GPU Config Manager:** Centralized hardware management for zero-config deployment.
*   **Batch Processing:** A command-line tool (`batch_process.py`) for processing thousands of files in bulk.
*   **Monitoring Dashboard:** Real-time metrics on extraction success rates and AI costs.

---

## 4. Technical Stack
- **Language:** Python 3.10+
- **OCR:** rostaing-ocr (GPU), pytesseract (CPU)
- **PDF Engines:** pdfplumber, PyMuPDF (fitz), pdf2image
- **AI Layers:** OpenAI GPT-4o / GPT-4o-mini
- **Hardware Acceleration:** CUDA (NVIDIA), DirectML (Intel), PyTorch, ONNX Runtime
- **Web Layer:** FastAPI / Flask (Unified Platform)

---
*Report Generated: May 19, 2026*
