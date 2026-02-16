"""
Enhanced Flask API Server for Insurance Form Extraction
Supports: Image display, text verification, schema extraction
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import tempfile
import json
from werkzeug.utils import secure_filename
from dataclasses import asdict
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Import enhanced extractor
try:
    from chunked_extractor import ChunkedInsuranceExtractor
    extractor = ChunkedInsuranceExtractor(api_key=os.getenv("OPENAI_API_KEY"))
    print("✓ Chunked Insurance Extractor initialized")
except Exception as e:
    print(f"❌ Error initializing extractor: {e}")
    print("   Make sure OPENAI_API_KEY is set")
    extractor = None

app = Flask(__name__)
CORS(app)

# Configuration
UPLOAD_FOLDER = tempfile.mkdtemp()
OUTPUT_FOLDER = "/outputs"
ALLOWED_EXTENSIONS = {'pdf'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create output folder
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def index(path):
    """Serve the main HTML interface or static assets"""
    # Prefer production build if available
    dist_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend', 'document-insight-engine-main', 'dist'))
    
    # If it's an API request, let the other routes handle it
    # Flask matches routes in order of definition, but since this is a catch-all,
    # we should check if it's explicitly an API path or a known file.
    if path.startswith('api/'):
        # This shouldn't be reached if API routes are defined, but just in case
        return jsonify({"error": "Not Found"}), 404

    if path != "" and os.path.exists(os.path.join(dist_dir, path)):
        return send_from_directory(dist_dir, path)
    
    # Default to index.html for the root or for SPA routing
    if os.path.exists(os.path.join(dist_dir, 'index.html')):
        return send_file(os.path.join(dist_dir, 'index.html'))
    
    # Fallback to dev index
    return send_file('index.html')


@app.route('/favicon.ico')
def favicon():
    """Handle favicon requests to prevent 404 errors"""
    return '', 204


@app.route('/api/extract-full', methods=['POST'])
def extract_full():
    """
    Enhanced endpoint: Extract with verification
    Returns: images, text, and schema
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Only PDF files are allowed'}), 400
    
    # Get optional target claim number
    target_claim = request.form.get('target_claim', None)
    
    try:
        # Save uploaded file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Process with enhanced extractor
        if not extractor:
            return jsonify({'error': 'Extractor not initialized'}), 500
        
        result = extractor.process_pdf_with_verification(filepath, target_claim)
        
        # Clean up uploaded file
        try:
            os.remove(filepath)
        except:
            pass
        
        # Convert paths to web-accessible URLs
        for page in result.get('pages', []):
            if 'image_path' in page:
                # Get relative path from output folder
                rel_path = os.path.relpath(page['image_path'], OUTPUT_FOLDER)
                page['image_url'] = f"/api/files/{rel_path}"
        
        return jsonify({
            'success': True,
            'data': result
        })
    
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Error in extract_full: {e}")
        print(error_trace)
        return jsonify({
            'error': str(e),
            'success': False,
            'traceback': error_trace
        }), 500


@app.route('/api/extract-batch', methods=['POST'])
def extract_batch():
    """
    Process multiple files
    Returns a list of results
    """
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files')
    target_claim = request.form.get('target_claim', None)
    
    results = []
    
    for file in files:
        if file.filename == '':
            continue
            
        if not allowed_file(file.filename):
            results.append({
                'filename': file.filename,
                'success': False,
                'error': 'Invalid file type'
            })
            continue
            
        try:
            # Save uploaded file
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Process
            result = extractor.process_pdf_with_verification(filepath, target_claim)
            
            # Clean up
            try:
                os.remove(filepath)
            except:
                pass
                
            # Convert paths to web-accessible URLs
            for page in result.get('pages', []):
                if 'image_path' in page:
                    rel_path = os.path.relpath(page['image_path'], OUTPUT_FOLDER)
                    page['image_url'] = f"/api/files/{rel_path}"
            
            results.append({
                'filename': file.filename,
                'success': True,
                'data': result
            })
            
        except Exception as e:
            results.append({
                'filename': file.filename,
                'success': False,
                'error': str(e)
            })
            
    # Flatten all claims from all successful results into a single list
    all_claims = []
    for r in results:
        if r['success'] and 'data' in r and 'extracted_schema' in r['data']:
            claims = r['data']['extracted_schema'].get('claims', [])
            filename = r.get('filename', 'unknown')
            
            for claim in claims:
                # Add source filename to each claim for traceability
                claim_copy = claim.copy()
                claim_copy['source_filename'] = filename
                all_claims.append(claim_copy)
    
    merged_filepath = None
    if all_claims:
        try:
            # Create a batch-specific folder for the merged results
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            batch_id = f"batch_{timestamp}"
            batch_dir = Path(OUTPUT_FOLDER) / batch_id
            batch_dir.mkdir(parents=True, exist_ok=True)
            
            merged_filename = "merged_results.json"
            merged_filepath = batch_dir / merged_filename
            
            with open(merged_filepath, 'w') as f:
                json.dump(all_claims, f, indent=4)
            
            print(f"✓ Merged results ({len(all_claims)} claims) saved to {merged_filepath}")
            
        except Exception as e:
            print(f"❌ Error saving merged results: {e}")

    return jsonify({
        'success': True,
        'results': results,
        'merged_file': f"/api/files/{batch_id}/{merged_filename}" if merged_filepath else None,
        'batch_id': batch_id if merged_filepath else None,
        'total_claims': len(all_claims) if all_claims else 0
    })


@app.route('/api/extract-schema-only', methods=['POST'])
def extract_schema_only():
    """
    Extract schema from user-provided/corrected text
    """
    data = request.get_json()
    
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text']
    target_claim = data.get('target_claim', None)
    
    try:
        if not extractor:
            return jsonify({'error': 'Extractor not initialized'}), 500
        
        schema = extractor.extract_schema_from_text(text, target_claim)
        
        return jsonify({
            'success': True,
            'schema': schema
        })
    
    except Exception as e:
        return jsonify({
            'error': str(e),
            'success': False
        }), 500


@app.route('/api/files/<path:filename>')
def serve_file(filename):
    """Serve extracted files (images, text, json)"""
    try:
        return send_from_directory(OUTPUT_FOLDER, filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 404


@app.route('/api/download/<session_id>/<file_type>')
def download_file(session_id, file_type):
    """
    Download specific file from extraction session
    file_type: 'text', 'schema', 'package'
    """
    session_dir = Path(OUTPUT_FOLDER) / f"extraction_{session_id}"
    
    if not session_dir.exists():
        return jsonify({'error': 'Session not found'}), 404
    
    file_map = {
        'text': 'extracted_text.txt',
        'schema': 'extracted_schema.json',
        'package': 'verification_package.json'
    }
    
    if file_type not in file_map:
        return jsonify({'error': 'Invalid file type'}), 400
    
    file_path = session_dir / file_map[file_type]
    
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(file_path, as_attachment=True)


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Enhanced Insurance Form Extractor API',
        'extractor': 'GPT-4 Vision Enhanced' if extractor else 'Not Initialized',
        'version': '2.0.0',
        'features': [
            'Layout-aware text extraction',
            'Image display',
            'Text verification',
            'Schema extraction',
            'Scanned document support',
            'Landscape/Portrait handling'
        ]
    })


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file too large error"""
    return jsonify({
        'error': 'File too large. Maximum size is 50MB'
    }), 413


if __name__ == '__main__':
    print("""
╔═══════════════════════════════════════════════════════════╗
║   Enhanced Insurance Form Extractor API Server           ║
║   AI-Powered Document Processing with Verification       ║
╚═══════════════════════════════════════════════════════════╝

Server starting...
Features:
  ✓ Layout-aware OCR
  ✓ Page-by-page image display
  ✓ Text verification interface
  ✓ Schema extraction from verified text
  ✓ Handles scanned & rotated documents

API Endpoints:
  POST /api/extract-full        - Full extraction with verification
  POST /api/extract-schema-only - Extract schema from provided text
  GET  /api/files/<path>         - Serve extracted images/files
  GET  /api/download/<id>/<type> - Download session files
  GET  /api/health               - Health check
  GET  /                         - Web interface

""")
    
    if not extractor:
        print("⚠ WARNING: Extractor not initialized!")
        print("   Set OPENAI_API_KEY environment variable")
        print("   API will return errors until key is set\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True)