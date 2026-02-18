from waitress import serve
from app import app
import os
from flask import send_from_directory

# Configure static serving for production
# This allows the backend to serve the frontend on the same port (5000)
dist_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "document-insight-engine-main", "dist"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting production server on port {port}...")
    serve(app, host="0.0.0.0", port=port)
