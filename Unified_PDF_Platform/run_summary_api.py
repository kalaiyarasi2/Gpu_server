import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from summary_api import router as summary_router

app = FastAPI(
    title="Data Retrieval Ingestion Verification Engine - Extension",
    description="Dedicated server for Trigger Points and AI Summaries",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(summary_router)

if __name__ == "__main__":
    print("\n" + "="*50)
    print("COGNETHRO API & TRIGGER EXTENSION STARTING")
    print("Dedicated Swagger UI at: http://localhost:8008/docs")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8008)
