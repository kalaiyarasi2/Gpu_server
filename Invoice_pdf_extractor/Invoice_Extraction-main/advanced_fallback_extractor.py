"""
Advanced Fallback Extractor
Used during validation if primary extraction logic (pdfplumber/PyMuPDF/Tesseract) yields empty/null data.
"""
import os
import fitz
import json
import logging
from pathlib import Path
from PIL import Image
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AdvancedFallbackExtractor:
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.is_native = self._check_if_native()
        self.extracted_text = ""

    def _check_if_native(self) -> bool:
        """
        Check if the PDF is natively text-based or just a scan.
        Threshold: average 50 chars per page means it has native text.
        """
        try:
            doc = fitz.open(self.pdf_path)
            num_pages = len(doc)
            total_text = ""
            for page in doc:
                total_text += page.get_text()
            doc.close()
            avg_chars = len(total_text) / max(1, num_pages)
            return avg_chars > 50
        except Exception as e:
            logger.error(f"Native check failed: {e}")
            return False

    def extract(self) -> str:
        """
        Executes the prioritized fallback logic based on the PDF type.
        """
        logger.info(f"Advanced Fallback: Processing {self.pdf_path}")
        logger.info(f"Advanced Fallback: Detected Native PDF = {self.is_native}")

        if self.is_native:
            text = self._extract_native_tables()
            if not text.strip():
                # If Camelot/Tabula yielded nothing usable, try fallback to PaddleOCR anyway
                logger.warning("Advanced Fallback: Native extraction yielded nothing, attempting scanned logic...")
                text = self._extract_scanned_ocr()
        else:
            text = self._extract_scanned_ocr()
            
        self.extracted_text = text
        return self.extracted_text

    def _extract_native_tables(self) -> str:
        """
        For native PDFs, use Camelot first (lattice for bordered, stream for whitespace), 
        and Tabula as fallback if Camelot fails.
        """
        try:
            import camelot
        except ImportError:
            logger.error("camelot-py not installed. Ensure ghostscript and camelot-py are configured.")
            return ""

        extracted_content = ""
        try:
            logger.info("Advanced Fallback: Attempting Camelot Lattice mode...")
            tables_lattice = camelot.read_pdf(self.pdf_path, pages='all', flavor='lattice')
            if len(tables_lattice) > 0:
                logger.info(f"Advanced Fallback: Camelot Lattice found {len(tables_lattice)} tables.")
                for i, t in enumerate(tables_lattice):
                    extracted_content += f"\n[CAMELOT_LATTICE_TABLE_{i+1}]\n"
                    # output in TSV format
                    df = t.df
                    extracted_content += df.to_csv(index=False, header=False, sep='\t')
            else:
                logger.info("Advanced Fallback: No tables found via Lattice, trying Camelot Stream mode...")
                tables_stream = camelot.read_pdf(self.pdf_path, pages='all', flavor='stream')
                if len(tables_stream) > 0:
                    logger.info(f"Advanced Fallback: Camelot Stream found {len(tables_stream)} tables.")
                    for i, t in enumerate(tables_stream):
                        extracted_content += f"\n[CAMELOT_STREAM_TABLE_{i+1}]\n"
                        df = t.df
                        extracted_content += df.to_csv(index=False, header=False, sep='\t')
        except Exception as e:
            logger.error(f"Advanced Fallback: Camelot failed: {e}")
            
            # fallback to Tabula
            try:
                import tabula
                logger.info("Advanced Fallback: Attempting Tabula as backup...")
                dfs = tabula.read_pdf(self.pdf_path, pages='all', multiple_tables=True)
                for i, df in enumerate(dfs):
                    extracted_content += f"\n[TABULA_TABLE_{i+1}]\n"
                    extracted_content += df.to_csv(index=False, header=True, sep='\t')
            except Exception as e2:
                logger.error(f"Advanced Fallback: Tabula also failed: {e2}")

        return extracted_content

    def _extract_scanned_ocr(self) -> str:
        """
        For scanned PDFs, use PaddleOCR for layout robustness.
        Using subprocess to avoid environment initialization conflicts.
        If PaddleOCR fails, fall back to Surya OCR.
        """
        import subprocess
        import tempfile
        import sys

        # 1. Try PaddleOCR first (as per recommendation)
        logger.info("Advanced Fallback: Attempting PaddleOCR via subprocess...")
        paddle_text = self._run_paddle_subprocess()
        if paddle_text:
            return paddle_text

        # 2. Try Surya OCR (as recommended for messy/scanned docs)
        logger.info("Advanced Fallback: PaddleOCR unsuccessful. Attempting Surya OCR via subprocess...")
        surya_text = self._run_surya_subprocess()
        if surya_text:
            return surya_text
            
        return ""

    def _run_paddle_subprocess(self) -> str:
        import subprocess
        import tempfile
        import sys
        
        # Create a temporary script to run PaddleOCR
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            script_path = f.name
            f.write(f"""
import sys
import os
import fitz
import numpy as np
import cv2
import traceback

# Disable OneDNN to avoid NotImplementedError on some CPUs
os.environ['FLAGS_use_onednn'] = '0'

try:
    from paddleocr import PaddleOCR
    # Minimal config for stability
    ocr = PaddleOCR(lang='en', use_angle_cls=False)
    
    doc = fitz.open(r'{self.pdf_path}')
    full_text = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=300)
        img_bytes = pix.tobytes("jpeg")
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        img_cv = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        
        # Simple extraction
        result = ocr.ocr(img_cv)
        
        page_text = f"[[PAGE_{{page_num+1}}]]\\n"
        if result and result[0]:
            for line in result[0]:
                if line and len(line) == 2:
                    page_text += line[1][0] + "\\n"
        full_text.append(page_text)
    doc.close()
    print("---DOC_START---")
    print("\\n".join(full_text))
except Exception as e:
    import traceback
    print(f"ERROR: {{e}}\\n{{traceback.format_exc()}}", file=sys.stderr)
    sys.exit(1)
""")

        try:
            my_env = os.environ.copy()
            result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=300, env=my_env)
            if result.returncode == 0 and "---DOC_START---" in result.stdout:
                return result.stdout.split("---DOC_START---")[1].strip()
            else:
                if result.stderr:
                    logger.error(f"PaddleOCR subprocess failed: {result.stderr}")
        except Exception as e:
            logger.error(f"PaddleOCR execution failed: {e}")
        finally:
            if os.path.exists(script_path): os.remove(script_path)
        return ""

    def _run_surya_subprocess(self) -> str:
        """
        Fallback for Surya OCR if PaddleOCR fails.
        Updated for Surya 0.17.x compatibility.
        """
        import subprocess
        import tempfile
        import sys
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            script_path = f.name
            f.write(f"""
import sys
import os
import fitz
import io
import traceback
from PIL import Image
try:
    # Surya 0.17.x API compatibility
    from surya.recognition import RecognitionPredictor
    from surya.detection import DetectionPredictor
    from surya.common.load import load_model, load_processor
    
    langs = ["en"]
    doc = fitz.open(r'{self.pdf_path}')
    
    # Load models (0.17.x style generic loading)
    det_model = load_model()
    det_processor = load_processor()
    rec_model = load_model("recognition")
    rec_processor = load_processor("recognition")
    
    det_predictor = DetectionPredictor()
    rec_predictor = RecognitionPredictor()
    
    full_text = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=300)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        
        line_predictions = det_predictor([img], det_model, det_processor)
        rec_predictions = rec_predictor([img], [langs], rec_model, rec_processor, line_predictions)
        
        page_text = f"[[PAGE_{{page_num+1}}]]\\n"
        if rec_predictions and len(rec_predictions) > 0:
            for line in rec_predictions[0].text_lines:
                page_text += line.text + "\\n"
        full_text.append(page_text)
    doc.close()
    print("---DOC_START---")
    print("\\n".join(full_text))
except Exception as e:
    import traceback
    print(f"ERROR: {{e}}\\n{{traceback.format_exc()}}", file=sys.stderr)
    sys.exit(1)
""")

        try:
            my_env = os.environ.copy()
            result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=600, env=my_env)
            if result.returncode == 0 and "---DOC_START---" in result.stdout:
                return result.stdout.split("---DOC_START---")[1].strip()
            else:
                if result.stderr:
                    logger.error(f"Surya subprocess failed: {result.stderr}")
        except Exception as e:
            logger.error(f"Surya execution failed: {e}")
        finally:
            if os.path.exists(script_path): os.remove(script_path)
        return ""

# For testing
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        extractor = AdvancedFallbackExtractor(sys.argv[1])
        output = extractor.extract()
        print("EXTRACTION PREVIEW:")
        print(output[:1000])
