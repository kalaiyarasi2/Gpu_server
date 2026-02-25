# coding: utf-8
"""
Centralized Swagger and OpenAPI documentation for the Unified PDF Platform.
This file contains docstrings, summaries, and custom JS/CSS configurations 
used for the API documentation.
"""

# --- Cognethro Trigger Point Documentation ---

COGNETHRO_SUMMARY = "Cognethro Trigger Point - Extract Document"

COGNETHRO_DESCRIPTION = """
The **Cognethro Trigger Point**.

- **Browser**: Visit `GET /cognethro` to open the interactive Swagger UI.
- **API/curl**: `POST /cognethro` with a `file` field to extract and get download URLs.
- **Direct Download**: Add `download=true` to your POST request to get a ZIP file containing both Excel and JSON directly as a single download.
"""

# --- Work Compensation Endpoint Documentation ---

WORK_COMP_SUMMARY = "Upload Workers Compensation PDF - Extract JSON"

WORK_COMP_DESCRIPTION = """
Dedicated endpoint for **Workers Compensation** documents (ACORD 130, CA, FL, and similar forms).

- **PDF only**: Upload a Workers Compensation PDF.
- **Returns**: Structured JSON containing demographics, premium calculations, and rating info.
- **Download**: A `{ } Download JSON` button will appear in the response below after extraction.
"""

# --- Global API Documentation ---

API_TITLE = "Data Retrieval Ingestion Verification Engine"
API_DESCRIPTION = "Unified API for Insurance Document Extraction"
API_VERSION = "1.0.0"

# --- Custom Swagger UI Enhancements ---

# Manually injected JS for download buttons in Swagger UI (Excel + JSON)
CUSTOM_SWAGGER_JS = """
<script>
window.addEventListener('load', function() {
    const observer = new MutationObserver(() => {
        // Target the specific pre/code blocks that show JSON responses
        const results = document.querySelectorAll('.microlight');
        results.forEach((node) => {
            const text = node.textContent || '';
            // Ensure we are in a response block and haven't injected yet
            const container = node.closest('.response');
            if (text.includes('"excel": "http') && container && !container.querySelector('.cognethro-dl-btns')) {
                try {
                    const data = JSON.parse(text);
                    const btnContainer = document.createElement('div');
                    btnContainer.className = 'cognethro-dl-btns';
                    btnContainer.style = 'margin-top: 15px; display: flex; gap: 10px; padding: 10px; background: #222; border-radius: 4px; border: 1px solid #444;';
                    
                    if (data.json) {
                        const jsBtn = document.createElement('a');
                        jsBtn.href = data.json;
                        jsBtn.textContent = '{ }  Download JSON';
                        jsBtn.style = 'background: #0c1a33; color: #93c5fd; border: 1px solid #1e40af; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px; cursor: pointer;';
                        jsBtn.setAttribute('download', data.output_json || 'result.json');
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

# JSON-only download button JS - used for the Work Compensation endpoint
WORK_COMP_SWAGGER_JS = """
<script>
window.addEventListener('load', function() {
    const observer = new MutationObserver(() => {
        const results = document.querySelectorAll('.microlight');
        results.forEach((node) => {
            const text = node.textContent || '';
            const container = node.closest('.response');
            if (text.includes('"json": "http') && container && !container.querySelector('.wc-dl-btns')) {
                try {
                    const data = JSON.parse(text);
                    if (!data.json) return;
                    const btnContainer = document.createElement('div');
                    btnContainer.className = 'wc-dl-btns';
                    btnContainer.style = 'margin-top: 15px; display: flex; gap: 10px; padding: 10px; background: #222; border-radius: 4px; border: 1px solid #444;';
                    
                    const jsBtn = document.createElement('a');
                    jsBtn.href = data.json;
                    jsBtn.textContent = '{ }  Download JSON';
                    jsBtn.style = 'background: #0c1a33; color: #93c5fd; border: 1px solid #1e40af; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px; cursor: pointer;';
                    jsBtn.setAttibute('download', data.output_json || 'result.json');
                    btnContainer.appendChild(jsBtn);
                    
                    node.parentElement.appendChild(btnContainer);
                } catch (e) {}
            }
        });
    });
    observer.observe(document.body, { childList: true, subtree: true });
});
</script>
"""
