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
- **Direct Download**: Add `download=true` to your POST request to get a ZIP file containing both Excel and JSON directly.
- **Downloads**: `Download Excel` and `Download JSON` buttons will appear in the response below after extraction.
"""

# --- Work Compensation Endpoint Documentation ---

WORK_COMP_SUMMARY = "Upload Workers Compensation PDF - Extract JSON"

WORK_COMP_DESCRIPTION = """
Dedicated endpoint for **Workers Compensation** documents (ACORD 130, CA, FL, and similar forms).

- **PDF only**: Upload a Workers Compensation PDF.
- **Returns**: Structured JSON containing demographics, premium calculations, and rating info.
- **Download**: `Download JSON` and `Download Excel` buttons will appear in the response below after extraction.
"""

# --- Bank Statement Documentation ---

BANK_STATEMENT_SUMMARY = "Upload Bank Statement PDF - Extract Financial Data"

BANK_STATEMENT_DESCRIPTION = """
Dedicated endpoint for **Bank Statements**.

- **PDF only**: Upload a bank statement PDF.
- **Returns**: Structured JSON and Excel links containing Deposits, Credits, Checks, and Debits.
- **Download**: Custom download buttons for both Excel and JSON will appear in the response.
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
    console.log("Standard Swagger UI Enhancements Loaded");
    
    const observeOptions = { childList: true, subtree: true };
    const observer = new MutationObserver((mutations) => {
        // Only target the innermost code elements to avoid matching parent + child (duplicate buttons)
        const blocks = document.querySelectorAll('pre, .microlight');
        blocks.forEach((container) => {
            const textContent = container.textContent || '';
            
            // Check if it contains the target URLs
            if (textContent.includes('"excel": "http') || textContent.includes('"json": "http')) {
                // Always attach to the parent element
                const targetElement = container.parentElement;
                
                if (targetElement && !targetElement.querySelector('.cognethro-dl-btns')) {
                    try {
                        const match = textContent.match(/\{[\s\S]*\}/);
                        if (!match) return;
                        
                        const data = JSON.parse(match[0]);
                        const btnContainer = document.createElement('div');
                        btnContainer.className = 'cognethro-dl-btns';
                        btnContainer.style = 'margin-top: 20px; display: flex; gap: 12px; padding: 15px; background: #111; border-radius: 10px; border: 1px solid #333; box-shadow: 0 4px 15px rgba(0,0,0,0.5);';
                        
                        if (data.excel) {
                            const xlBtn = document.createElement('a');
                            xlBtn.href = data.excel;
                            xlBtn.innerHTML = '⚡  <b>Download Excel</b>';
                            xlBtn.style = 'background: linear-gradient(135deg, #064e3b 0%, #065f46 100%); color: #6ee7b7; border: 1px solid #059669; padding: 12px 22px; border-radius: 8px; text-decoration: none; font-size: 14px; cursor: pointer; transition: transform 0.1s;';
                            xlBtn.onmouseover = () => xlBtn.style.transform = 'scale(1.02)';
                            xlBtn.onmouseout = () => xlBtn.style.transform = 'scale(1)';
                            xlBtn.setAttribute('download', data.output_file || 'result.xlsx');
                            btnContainer.appendChild(xlBtn);
                        }
                        
                        if (data.json) {
                            const jsBtn = document.createElement('a');
                            jsBtn.href = data.json;
                            jsBtn.innerHTML = '📂  <b>Download JSON</b>';
                            jsBtn.style = 'background: linear-gradient(135deg, #0c1a33 0%, #112244 100%); color: #93c5fd; border: 1px solid #1e40af; padding: 12px 22px; border-radius: 8px; text-decoration: none; font-size: 14px; cursor: pointer; transition: transform 0.1s;';
                            jsBtn.onmouseover = () => jsBtn.style.transform = 'scale(1.02)';
                            jsBtn.onmouseout = () => jsBtn.style.transform = 'scale(1)';
                            jsBtn.setAttribute('download', data.output_json || 'result.json');
                            btnContainer.appendChild(jsBtn);
                        }
                        
                        targetElement.appendChild(btnContainer);
                    } catch (e) {
                        // Silent fail for non-JSON or partial text
                    }
                }
            }
        });
    });
    
    observer.observe(document.body, observeOptions);
});
</script>
"""

# JSON-only download button JS - used for the Work Compensation endpoint
WORK_COMP_SWAGGER_JS = """
<script>
window.addEventListener('load', function() {
    console.log("WC Swagger UI Enhancements Loaded");
    
    const observeOptions = { childList: true, subtree: true };
    const observer = new MutationObserver((mutations) => {
        // Only target the innermost code elements to avoid matching parent + child (duplicate buttons)
        const blocks = document.querySelectorAll('pre, .microlight');
        blocks.forEach((container) => {
            const textContent = container.textContent || '';
            
            if (textContent.includes('"json": "http') || textContent.includes('"excel": "http')) {
                // Always attach to the parent element
                const targetElement = container.parentElement;
                
                if (targetElement && !targetElement.querySelector('.wc-dl-btns')) {
                    try {
                        const match = textContent.match(/\{[\s\S]*\}/);
                        if (!match) return;
                        
                        const data = JSON.parse(match[0]);
                        const btnContainer = document.createElement('div');
                        btnContainer.className = 'wc-dl-btns';
                        btnContainer.style = 'margin-top: 20px; display: flex; gap: 12px; padding: 15px; background: #111; border-radius: 10px; border: 1px solid #333; box-shadow: 0 4px 15px rgba(0,0,0,0.5);';
                        
                        if (data.excel) {
                            const xlBtn = document.createElement('a');
                            xlBtn.href = data.excel;
                            xlBtn.innerHTML = '⚡  <b>Download Excel</b>';
                            xlBtn.style = 'background: linear-gradient(135deg, #064e3b 0%, #065f46 100%); color: #6ee7b7; border: 1px solid #059669; padding: 12px 22px; border-radius: 8px; text-decoration: none; font-size: 14px; cursor: pointer; transition: transform 0.1s;';
                            xlBtn.onmouseover = () => xlBtn.style.transform = 'scale(1.02)';
                            xlBtn.onmouseout = () => xlBtn.style.transform = 'scale(1)';
                            xlBtn.setAttribute('download', data.output_file || 'result.xlsx');
                            btnContainer.appendChild(xlBtn);
                        }
                        
                        if (data.json) {
                            const jsBtn = document.createElement('a');
                            jsBtn.href = data.json;
                            jsBtn.innerHTML = '📂  <b>Download JSON</b>';
                            jsBtn.style = 'background: linear-gradient(135deg, #0c1a33 0%, #112244 100%); color: #93c5fd; border: 1px solid #1e40af; padding: 12px 22px; border-radius: 8px; text-decoration: none; font-size: 14px; cursor: pointer; transition: transform 0.1s;';
                            jsBtn.onmouseover = () => jsBtn.style.transform = 'scale(1.02)';
                            jsBtn.onmouseout = () => jsBtn.style.transform = 'scale(1)';
                            jsBtn.setAttribute('download', data.output_json || 'result.json');
                            btnContainer.appendChild(jsBtn);
                        }
                        
                        targetElement.appendChild(btnContainer);
                    } catch (e) {
                        // Silent fail
                    }
                }
            }
        });
    });
    
    observer.observe(document.body, observeOptions);
});
</script>
"""
