import asyncio
import httpx
import time

API_BASE = "http://localhost:8007"

async def test_health():
    async with httpx.AsyncClient() as client:
        start = time.time()
        try:
            response = await client.get(f"{API_BASE}/api/health")
            elapsed = time.time() - start
            print(f"[Health Check] Status: {response.status_code}, Time: {elapsed:.2f}s")
            return response.json()
        except Exception as e:
            print(f"[Health Check] Failed: {e}")
            return None

async def test_extract():
    async with httpx.AsyncClient() as client:
        # Use a dummy file to test
        file_path = "c:\\Users\\INTERN\\main_project\\Main--main\\Unified_PDF_Platform\\uploads\\365 Properties UHC 1.1-1.31_raw_extracted.txt"
        if not os.path.exists(file_path):
            print(f"[Extract Test] File not found: {file_path}")
            return
            
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "text/plain")}
            start = time.time()
            try:
                response = await client.post(f"{API_BASE}/api/extract", files=files)
                elapsed = time.time() - start
                print(f"[Extract Test] Status: {response.status_code}, Time: {elapsed:.2f}s")
                if response.status_code == 422:
                    print(f"[Extract Test] Validation Error Detail: {response.text}")
                return response.json()
            except Exception as e:
                print(f"[Extract Test] Failed: {e}")
                return None

async def main():
    print("--- Starting Concurrency & Extraction Test ---")
    print("Note: This test assumes the server is running on localhost:8000")
    
    # We can't easily trigger a long extraction without a file, 
    # but we can check if the server is responsive while it's processing.
    # If the fix works, health checks should return nearly instantly 
    # regardless of other heavy processing.
    
    tasks = [test_health(), test_extract()]
    results = await asyncio.gather(*tasks)
    
    print("\nAll health checks completed.")

if __name__ == "__main__":
    asyncio.run(main())
