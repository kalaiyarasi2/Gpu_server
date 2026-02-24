"""
Centralized Swagger and OpenAPI documentation for the Unified PDF Platform.
This file contains docstrings, summaries, and custom JS/CSS configurations 
used for the API documentation.
"""

# --- Cognethro Trigger Point Documentation ---

COGNETHRO_SUMMARY = "Cognethro Trigger Point — Extract Document"

COGNETHRO_DESCRIPTION = """
The **Cognethro Trigger Point**.

- **Browser**: Visit `GET /cognethro` to open the interactive Swagger UI.
- **API/curl**: `POST /cognethro` with a `file` field to extract and get download URLs.
- **Direct Download**: Add `download=true` to your POST request to get a ZIP file containing both Excel and JSON directly as a single download.
"""

# --- Global API Documentation ---

API_TITLE = "Data Retrieval Ingestion Verification Engine"
API_DESCRIPTION = "Unified API for Insurance Document Extraction"
API_VERSION = "1.0.0"

# --- Custom Swagger UI Enhancements ---

# Manually injected JS for download buttons in Swagger UI
CUSTOM_SWAGGER_JS = """
<script>
window.addEventListener('load', function() {
    const observer = new MutationObserver(() => {
        const results = document.querySelectorAll('.response .microlight');
        results.forEach((node) => {
            const text = node.textContent;
            if (text.includes('"excel": "http') && !node.parentElement.querySelector('.cognethro-dl-btns')) {
                try {
                    const data = JSON.parse(text);
                    const btnContainer = document.createElement('div');
                    btnContainer.className = 'cognethro-dl-btns';
                    btnContainer.style = 'margin-top: 15px; display: flex; gap: 10px; padding: 10px; background: #222; border-radius: 4px; border: 1px solid #444;';
                    
                    if (data.excel) {
                        const exBtn = document.createElement('a');
                        exBtn.href = data.excel;
                        exBtn.textContent = '📊 Download Excel';
                        exBtn.style = 'background: #052e16; color: #4ade80; border: 1px solid #166534; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px;';
                        exBtn.download = data.output_file || 'result.xlsx';
                        btnContainer.appendChild(exBtn);
                    }
                    
                    if (data.json) {
                        const jsBtn = document.createElement('a');
                        jsBtn.href = data.json;
                        jsBtn.textContent = '{ } Download JSON';
                        jsBtn.style = 'background: #0c1a33; color: #93c5fd; border: 1px solid #1e40af; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px;';
                        jsBtn.download = data.output_json || 'result.json';
                        btnContainer.appendChild(jsBtn);
                    }
                    
                    node.parentElement.appendChild(btnContainer);
                } catch (e) {}
            }
        });
    });
    observer.observe(document.body, { childList: true, subtree: true });
});
</script>
"""
