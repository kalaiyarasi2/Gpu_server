from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List
import os
from dotenv import load_dotenv

# Load environment variables from backend/.env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from statement_extractor import StatementExtractor


app = FastAPI(title="Bank Statement Extractor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/extract")
async def extract_statements(files: List[UploadFile] = File(...)) -> List[Dict[str, Any]]:
    """
    Upload one or more PDF bank statements and extract deposits / checks & debits sequentially.
    Returns a list of extraction results.
    """
    results = []
    extractor = StatementExtractor(output_dir="outputs")

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            results.append({
                "filename": file.filename,
                "status": "error",
                "error": "Only PDF files are supported"
            })
            continue

        # Save upload to a temp file
        suffix = ".pdf"
        temp_path = None
        try:
            with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await file.read()
                tmp.write(content)
                temp_path = Path(tmp.name)

            result = extractor.process_pdf(str(temp_path))
            results.append({
                "filename": file.filename,
                "status": "success",
                "session_dir": result["session_dir"],
                "files": {
                    "json": result["json_file"],
                    "excel": result["excel_file"],
                    "verification": result["verification_file"],
                    "text": result["extracted_text_file"],
                },
                "data": result["data"],
            })
        except Exception as e:
            results.append({
                "filename": file.filename,
                "status": "error",
                "error": str(e)
            })
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
    
    return results


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8004)

