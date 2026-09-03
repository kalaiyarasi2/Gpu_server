import os
import shutil
import json
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv

# Import our extraction logic (V3)
import universal_pdf_extractor_v3 as extractor

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="PDF Invoice Extractor")

# Configure CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with specific React URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup directories
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Mount static and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class ExtractionResult(BaseModel):
    filename: str
    row_count: int
    output_file: str
    output_json: Optional[str] = None
    preview_data: Optional[List[dict]] = None

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index_v4.html", {"request": request})

@app.post("/api/extract")
async def extract_invoice(file: UploadFile = File(...)):
    print(f"\n[V3][API] Received extraction request for: {file.filename}")
    if not file.filename.lower().endswith(".pdf"):
        print(f"[V3][API] Error: Not a PDF")
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    file_path = UPLOAD_DIR / file.filename
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Initialize OpenAI client
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Step 1 & 2 combined for simplicity in UI, but we can reuse the logic
        # We'll use the direct extraction method for the UI
        data = extractor.process_single_pdf(str(file_path), client)
        
        # Flatten data for Excel and Preview
        rows = extractor.flatten_extracted_data(data, file.filename)
            
        if not rows:
            print(f"[V3][API] No rows extracted from {file.filename}")
            # Still create an empty excel with headers
            df = pd.DataFrame(columns=['SOURCE_FILE'] + extractor.REQUIRED_FIELDS)
        else:
            df = pd.DataFrame(rows)
            # Reorder columns
            cols = ['SOURCE_FILE'] + extractor.REQUIRED_FIELDS
            # Ensure all required fields exist
            for col in extractor.REQUIRED_FIELDS:
                if col not in df.columns:
                    df[col] = None
            df = df[cols]
        
        output_filename = f"{Path(file.filename).stem}_extracted.xlsx"
        output_path = OUTPUT_DIR / output_filename
        df.to_excel(output_path, index=False, engine='openpyxl')
        
        # Save as JSON as well
        json_filename = f"{Path(file.filename).stem}_extracted.json"
        json_path = OUTPUT_DIR / json_filename
        with open(json_path, "w") as f:
            json.dump(rows, f, indent=4)
        
        print(f"[V3][API] Extraction successful. Returning {len(rows)} rows.")
        return ExtractionResult(
            filename=file.filename,
            row_count=len(rows),
            output_file=output_filename,
            output_json=json_filename,
            preview_data=rows[:5] # Send first 5 rows for preview
        )
        
    except Exception as e:
        print(f"Extraction Error: {e}")
        
        # Propagate OpenAI Quota errors
        error_msg = str(e)
        if "insufficient_quota" in error_msg.lower() or "429" in error_msg:
            raise HTTPException(
                status_code=429, 
                detail="Your OpenAI API quota is exhausted. Please check your billing/usage limits."
            )
            
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # We keep the files for download, but we could cleanup old ones later
        pass

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if filename.endswith(".xlsx"):
        media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif filename.endswith(".json"):
        media_type = 'application/json'
    else:
        media_type = 'application/octet-stream'
        
    return FileResponse(path=file_path, filename=filename, media_type=media_type)

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("V3 SERVER STARTING")
    print("Access the UI at: http://localhost:8005")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8005)
