import os
import sys
import subprocess
import json
import re
from pathlib import Path
import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv
import fitz  # PyMuPDF

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Configure Poppler PATH for pdf2image (OCR support)
POPPLER_PATH = os.getenv("POPPLER_PATH")
if POPPLER_PATH and os.path.exists(POPPLER_PATH):
    os.environ["PATH"] = POPPLER_PATH + os.pathsep + os.environ.get("PATH", "")
    print(f"✓ Poppler PATH configured: {POPPLER_PATH}")
else:
    print("⚠️ Warning: POPPLER_PATH not set or invalid. OCR may not work for scanned PDFs.")

# Add Insurance backend to Python path for module imports
BASE_DIR = Path(__file__).parent
INSURANCE_BACKEND_DIR = BASE_DIR.parent / "Insurance_pdf_extractor-main/backend"
sys.path.insert(0, str(INSURANCE_BACKEND_DIR))

# Import Insurance extractor as module
try:
    from chunked_extractor import ChunkedInsuranceExtractor
    INSURANCE_MODULE_AVAILABLE = True
    print("✓ Insurance extractor module loaded successfully")
except ImportError as e:
    INSURANCE_MODULE_AVAILABLE = False
    print(f"⚠️ Warning: Could not import Insurance extractor module: {e}")
    print("   Will fall back to subprocess method if needed.")

# Configuration for paths
INVOICE_SCRIPT = BASE_DIR.parent / "Invoice_pdf_extractor/Invoice_Extraction-main/universal_pdf_extractor_v3.py"
STRUCTURAL_INVOICE_SCRIPT = BASE_DIR.parent / "structural_pdf_extractor.py"  # NEW: Structural layer
INSURANCE_SCRIPT = BASE_DIR.parent / "Insurance_pdf_extractor-main/backend/chunked_extractor.py"
INSURANCE_OUTPUT_DIR = INSURANCE_BACKEND_DIR / "outputs"
OUTPUT_BASE = BASE_DIR / "unified_outputs"
OUTPUT_BASE.mkdir(exist_ok=True)

class UnifiedRouter:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Initialize Insurance extractor if module is available
        if INSURANCE_MODULE_AVAILABLE:
            try:
                self.insurance_extractor = ChunkedInsuranceExtractor(
                    api_key=OPENAI_API_KEY,
                    output_dir=str(INSURANCE_OUTPUT_DIR)
                )
                print("✓ ChunkedInsuranceExtractor initialized")
            except Exception as e:
                print(f"⚠️ Warning: Could not initialize Insurance extractor: {e}")
                self.insurance_extractor = None
        else:
            self.insurance_extractor = None

    def extract_snippet(self, pdf_path, max_pages=2):
        """Extract first few pages of text for classification using PyMuPDF."""
        try:
            doc = fitz.open(pdf_path)
            text = ""
            for i in range(min(len(doc), max_pages)):
                text += doc[i].get_text() or ""
            doc.close()
            return text.strip()[:4000]
        except Exception as e:
            print(f"[Router] Error extracting snippet: {e}")
            return ""

    def classify_document(self, pdf_path):
        """Use LLM to strictly classify the document, using text and filename hints."""
        print("\n" + "="*70)
        print("🧠 STEP 1: INTELLIGENT DOCUMENT CLASSIFICATION")
        print("="*70)
        
        filename = Path(pdf_path).name
        print(f"📄 Processing: {filename}")
        
        print("\n🔍 Extracting text snippet for classification...")
        text = self.extract_snippet(pdf_path)
        
        # Heuristic: If text is mostly dots or very short, it's likely a scan/corrupt layer
        is_noisy = False
        if not text or len(re.sub(r'[^a-zA-Z0-9]', '', text)) < 50:
            is_noisy = True
            print("⚠️  Warning: Extracted text is poor/noisy. Relying on filename and visual cues.")

        print(f"\n📝 Text Preview (first 400 chars):\n{'-'*70}\n{text[:400]}\n{'-'*70}")

        prompt = f"""Analyze the following document metadata and text to classify its type.

FILENAME: {filename}
EXTRACTED TEXT (MAY BE NOISY/SCANNED):
{text if not is_noisy else "[TEXT LAYER CORRUPTED OR SCANNED - USE FILENAME HINT]"}

CLASSIFICATION RULES:
1. **INVOICE**: Financial invoice, billing statement, premium notice, health insurance bill, payment receipt
2. **INSURANCE**: Insurance claim, loss run report, claim summary, workers compensation report, liability report
3. **Filename Analysis**: If text is corrupted/noisy, analyze the filename for keywords:
   - Keywords suggesting INSURANCE: "claim", "loss", "run", "comp", "liability", "CCMSI", "BerkleyNet", "AmTrust", "workers"
   - Keywords suggesting INVOICE: "invoice", "bill", "payment", "premium", "statement"
4. **Default for Insurance Carriers**: If filename contains known insurance carrier names (CCMSI, BerkleyNet, AmTrust, Sedgwick, etc.), classify as INSURANCE
5. Output MUST be exactly ONE word: INVOICE or INSURANCE
6. If completely uncertain, prefer INSURANCE for documents with carrier names

OUTPUT:"""

        try:
            print("\n🤖 Sending to AI for classification...")
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            classification = response.choices[0].message.content.strip().upper()
            print(f"\n✅ AI Response: {classification}")

            if "INVOICE" in classification:
                print("\n📊 Classification Result: INVOICE")
                print("   → Will route to Invoice Extractor")
                return "INVOICE"
            elif "INSURANCE" in classification:
                print("\n🏥 Classification Result: INSURANCE")
                print("   → Will route to Insurance Extractor")
                return "INSURANCE"
            else:
                print(f"\n❓ Classification Result: UNKNOWN")
                print(f"   AI returned: {classification}")
                print("   → Cannot determine document type")
                return "UNKNOWN"
        except Exception as e:
            print(f"\n❌ Classification Error: {e}")
            return "UNKNOWN"

    def run_invoice_extractor(self, pdf_path, use_structural=False):
        """Run the invoice extractor on the PDF.
        
        Args:
            pdf_path: Path to the PDF file
            use_structural: If True, use the structural analysis layer for better accuracy.
                          Default is False - use standard extractor first.
        """
        print("\n" + "="*70)
        print("📊 STEP 2: RUNNING INVOICE EXTRACTOR")
        print("="*70)
        print(f"📂 Input: {pdf_path}")
        
        # Choose extraction method
        if use_structural and STRUCTURAL_INVOICE_SCRIPT.exists():
            print(f"🔧 Method: Structural Analysis Layer (Enhanced)")
            print(f"🔧 Script: {STRUCTURAL_INVOICE_SCRIPT}")
            script_to_use = STRUCTURAL_INVOICE_SCRIPT
            output_xlsx = OUTPUT_BASE / f"{Path(pdf_path).stem}_invoice_structural.xlsx"
        else:
            print(f"🔧 Method: Standard Extraction")
            print(f"🔧 Script: {INVOICE_SCRIPT}")
            script_to_use = INVOICE_SCRIPT
            output_xlsx = OUTPUT_BASE / f"{Path(pdf_path).stem}_invoice.xlsx"
        
        print("\n⏳ Processing... (this may take 30-60 seconds)\n")

        try:
            # Wrapper to run process with line-by-line output for debugging hangs
            def run_with_logging(cmd, timeout_secs):
                print(f"  [Debug] Running command: {' '.join(cmd)}")
                try:
                    import subprocess
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env={"PYTHONIOENCODING": "utf-8", **os.environ},
                        encoding="utf-8",
                        bufsize=1,
                        universal_newlines=True
                    )
                    
                    full_stdout = []
                    full_stderr = []
                    
                    import threading
                    def stream_reader(pipe, log_label, collector):
                        for line in iter(pipe.readline, ""):
                            print(f"    [{log_label}] {line.strip()}")
                            collector.append(line)
                    
                    t1 = threading.Thread(target=stream_reader, args=(process.stdout, "OUT", full_stdout))
                    t2 = threading.Thread(target=stream_reader, args=(process.stderr, "ERR", full_stderr))
                    t1.start()
                    t2.start()
                    
                    # Wait with timeout
                    try:
                        process.wait(timeout=timeout_secs)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        raise subprocess.TimeoutExpired(cmd, timeout_secs)
                    
                    t1.join()
                    t2.join()
                    
                    class Result:
                        def __init__(self, stdout, stderr, returncode):
                            self.stdout = "".join(stdout)
                            self.stderr = "".join(stderr)
                            self.returncode = returncode
                            
                    return Result(full_stdout, full_stderr, process.returncode)
                except Exception as e:
                    raise e

            # For structural extractor, output file is auto-named
            if use_structural and script_to_use == STRUCTURAL_INVOICE_SCRIPT:
                result = run_with_logging(["python", str(script_to_use), str(pdf_path)], 900)
                # Structural extractor creates its own output file
                output_xlsx = Path(pdf_path).parent / "extracted_data_structural.xlsx"
            else:
                result = run_with_logging(["python", str(script_to_use), str(pdf_path), str(output_xlsx)], 900)
            
            if result.returncode != 0:
                print(f"\n❌ Extraction Failed (Exit Code: {result.returncode})")
                print(f"Error Details:\n{result.stderr}")
                return {"error": f"Invoice extraction failed: {result.stderr}"}
            
            print("✅ Invoice extractor completed successfully!")
            print("\n🔍 Verifying generated files...")
            
            if not output_xlsx.exists():
                print(f"\n❌ Error: Expected Excel output file not found at {output_xlsx}")
                print(f"   Stdout: {result.stdout}")
                return {"error": "Excel output not found"}
            
            # Move the file to unified_outputs for consistency
            final_output = OUTPUT_BASE / output_xlsx.name
            if output_xlsx != final_output:
                import shutil
                shutil.copy2(output_xlsx, final_output)
                output_xlsx = final_output
            
            print(f"\n📊 Excel File: {output_xlsx.name}")
            print(f"   Location: {output_xlsx}")
            
            return {"type": "INVOICE", "excel": str(output_xlsx), "json": self.xlsx_to_json(output_xlsx)}
        except subprocess.TimeoutExpired:
            print(f"\n❌ Invoice Extraction Failed: Timeout after 900 seconds.")
            return {"error": "Invoice extraction timed out."}
        except Exception as e:
            print(f"\n❌ Invoice Extraction Error: {e}")
            return {"error": str(e)}

    def run_insurance_extractor(self, pdf_path):
        """Run the insurance extractor using direct module import (preferred) or subprocess fallback."""
        print("\n" + "="*70)
        print("🏥 STEP 2: RUNNING INSURANCE EXTRACTOR")
        print("="*70)
        print(f"📂 Input: {pdf_path}")
        
        # Method 1: Direct module import (PREFERRED)
        if self.insurance_extractor:
            print(f"🔧 Method: Direct Module Import (ChunkedInsuranceExtractor)")
            print("\n⏳ Processing... (this may take 1-2 minutes)\n")
            
            try:
                # Call the main processing method
                result = self.insurance_extractor.process_pdf_with_verification(
                    pdf_path=pdf_path,
                    target_claim_number=None  # Extract all claims
                )
                
                print("✅ Insurance extractor completed successfully!")
                print("\n🔍 Locating output files...")
                
                # Extract session information from result
                session_id = result.get("session_id")
                session_dir = Path(result.get("session_dir"))
                schema_file = session_dir / "extracted_schema.json"
                
                if schema_file.exists():
                    print(f"\n✅ Found JSON output: {schema_file.name}")
                    print(f"   Location: {schema_file}")
                    print("\n🔄 Converting JSON to Excel...")
                    excel_path = self.json_to_xlsx(schema_file)
                    print(f"✅ Excel File: {Path(excel_path).name}")
                    print("\n" + "="*70)
                    print("✅ INSURANCE EXTRACTION COMPLETE")
                    print("="*70)
                    return {
                        "type": "INSURANCE",
                        "json": str(schema_file),
                        "excel": excel_path,
                        "session_id": session_id,
                        "session_dir": str(session_dir)
                    }
                else:
                    print(f"\n❌ Error: Expected schema file not found at {schema_file}")
                    return {"error": "Schema file not found after extraction"}
                    
            except Exception as e:
                print(f"\n❌ Insurance Extraction Error: {e}")
                import traceback
                traceback.print_exc()
                return {"error": f"Insurance extraction failed: {str(e)}"}
        
        # Method 2: Subprocess fallback (if module import failed)
        else:
            print(f"🔧 Method: Subprocess (Fallback)")
            print(f"🔧 Script: {INSURANCE_SCRIPT}")
            print("\n⏳ Processing... (this may take 1-2 minutes)\n")
            
            result = subprocess.run(
                ["python", str(INSURANCE_SCRIPT), str(pdf_path)],
                capture_output=True,
                text=True,
                cwd=str(INSURANCE_SCRIPT.parent),
                env={"PYTHONIOENCODING": "utf-8", **os.environ},
                encoding="utf-8"
            )
            
            if result.returncode == 0:
                print("✅ Insurance extractor completed successfully!")
                print("\n🔍 Searching for most recent extraction folder...")
                insurance_out_dir = INSURANCE_SCRIPT.parent / "outputs"
                
                if insurance_out_dir.exists():
                    folders = list(insurance_out_dir.glob("extraction_*"))
                    if folders:
                        folders.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                        latest_folder = folders[0]
                        schema_json = latest_folder / "extracted_schema.json"
                        
                        if schema_json.exists():
                            print(f"✅ Found JSON output: {schema_json.name}")
                            print(f"   Location: {schema_json}")
                            print("\n🔄 Converting JSON to Excel...")
                            excel_path = self.json_to_xlsx(schema_json)
                            print(f"✅ Excel File: {Path(excel_path).name}")
                            print("\n" + "="*70)
                            print("✅ INSURANCE EXTRACTION COMPLETE")
                            print("="*70)
                            return {"type": "INSURANCE", "json": str(schema_json), "excel": excel_path}
                
                print("\n❌ Error: Could not find output JSON.")
                return {"error": "Output JSON not found", "stdout": result.stdout}
            else:
                print(f"\n❌ Insurance Extraction Failed (Exit Code: {result.returncode})")
                print(f"Error Details:\n{result.stderr}")
                return {"error": result.stderr}

    def xlsx_to_json(self, xlsx_path):
        """Convert Excel output to JSON."""
        try:
            df = pd.read_excel(xlsx_path)
            json_path = xlsx_path.with_suffix(".json")
            df.to_json(json_path, orient="records", indent=4)
            return str(json_path)
        except Exception as e:
            print(f"[Router] Excel to JSON conversion failed: {e}")
            return None

    def json_to_xlsx(self, json_path):
        """Convert JSON output to Excel."""
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            if isinstance(data, dict) and "claims" in data:
                rows = data["claims"]
            elif isinstance(data, list):
                rows = data
            else:
                rows = [data]
                
            df = pd.DataFrame(rows)
            xlsx_path = Path(json_path).with_suffix(".xlsx")
            df.to_excel(xlsx_path, index=False)
            return str(xlsx_path)
        except Exception as e:
            print(f"[Router] JSON to Excel conversion failed: {e}")
            return None

    def process(self, pdf_path):
        """Main entry point: classify and route the document."""
        print("\n" + "="*70)
        print("🚀 UNIFIED PDF INTELLIGENT ROUTER")
        print("="*70)
        print(f"📥 Input PDF: {Path(pdf_path).name}")
        print(f"🕐 Started: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        
        # Step 1: Classify
        doc_type = self.classify_document(pdf_path)
        
        if doc_type == "UNKNOWN":
            print("\n" + "="*70)
            print("❌ PROCESSING FAILED: UNKNOWN DOCUMENT TYPE")
            print("="*70)
            return {"error": "Could not classify document type"}
        
        # Step 2: Route to appropriate extractor
        if doc_type == "INVOICE":
            # TRY 1: Standard Extractor
            result = self.run_invoice_extractor(pdf_path, use_structural=False)
            
            # FALLBACK: If standard extraction yielded no data or failed, try structural
            should_fallback = False
            
            # 1. Proactive Detection: Is this a Guardian or GIS 23 invoice?
            is_guardian = False
            is_gis23 = False
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    first_page_text = (pdf.pages[0].extract_text() or "").lower()
                    if "guardian" in first_page_text:
                        is_guardian = True
                        print("🛡️  Guardian invoice detected proactively.")
                    if "gis 23" in first_page_text or "restaurant services" in first_page_text:
                        is_gis23 = True
                        print("🍽️  GIS 23 Restaurant Services invoice detected proactively.")
            except Exception as e:
                print(f"  [Router] Detection failed: {e}")

            if "error" in result:
                should_fallback = True
            else:
                try:
                    df = pd.read_excel(result["excel"])
                    if len(df) <= 1: # Only header or empty
                        should_fallback = True
                    
                    # 2. Force fallback for complex invoices to ensure accuracy and prevent standard timeouts
                    if is_guardian or is_gis23:
                         should_fallback = True
                         reason = "Guardian" if is_guardian else "GIS 23"
                         print(f"⚠️  {reason} invoice: Forcing Structural layer for maximum accuracy...")
                except:
                    should_fallback = True
            
            if should_fallback:
                print("\n⚠️  Standard extraction yielded insufficient results. Falling back to Structural Layer...")
                structural_result = self.run_invoice_extractor(pdf_path, use_structural=True)
                if "error" not in structural_result:
                    result = structural_result
                else:
                    print(f"❌ Structural fallback also failed: {structural_result.get('error')}")

        elif doc_type == "INSURANCE":
            result = self.run_insurance_extractor(pdf_path)
        else:
            print("\n" + "="*70)
            print(f"❌ PROCESSING FAILED: UNSUPPORTED TYPE '{doc_type}'")
            print("="*70)
            return {"error": f"Unsupported document type: {doc_type}"}
        
        # Final summary
        if "error" not in result:
            print("\n" + "="*70)
            print("🎉 PROCESSING COMPLETE - SUCCESS!")
            print("="*70)
            print(f"📊 Document Type: {result.get('type')}")
            print(f"📁 Excel File: {Path(result.get('excel', '')).name if result.get('excel') else 'N/A'}")
            print(f"📄 JSON File: {Path(result.get('json', '')).name if result.get('json') else 'N/A'}")
            print(f"🕐 Completed: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*70 + "\n")
        else:
            print("\n" + "="*70)
            print("❌ PROCESSING FAILED")
            print("="*70)
            print(f"Error: {result.get('error')}")
            print("="*70 + "\n")
        
        return result

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python unified_router.py <pdf_path>")
        sys.exit(1)
    
    router = UnifiedRouter()
    result = router.process(sys.argv[1])
    print("\n" + "="*50)
    print("UNIFIED ROUTER RESULT")
    print("="*50)
    print(json.dumps(result, indent=2))
