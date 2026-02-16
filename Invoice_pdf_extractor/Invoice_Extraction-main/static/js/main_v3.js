document.addEventListener('DOMContentLoaded', () => {
    // Theme Toggle Logic
    const themeToggle = document.getElementById('theme-toggle');
    const themeIcon = themeToggle.querySelector('i');

    // Check for saved theme or system preference
    const savedTheme = localStorage.getItem('invoice-ai-theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    if (savedTheme === 'dark' || (!savedTheme && systemPrefersDark)) {
        document.body.classList.add('dark-mode');
        updateThemeIcon(true);
    } else {
        updateThemeIcon(false);
    }

    themeToggle.addEventListener('click', () => {
        const isDark = document.body.classList.toggle('dark-mode');
        localStorage.setItem('invoice-ai-theme', isDark ? 'dark' : 'light');
        updateThemeIcon(isDark);
    });

    function updateThemeIcon(isDark) {
        if (isDark) {
            themeIcon.classList.remove('fa-moon');
            themeIcon.classList.add('fa-sun');
        } else {
            themeIcon.classList.remove('fa-sun');
            themeIcon.classList.add('fa-moon');
        }
    }

    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const extractBtn = document.getElementById('extract-btn');
    const statusDiv = document.getElementById('status');
    const statusText = document.getElementById('status-text');
    const resultsArea = document.getElementById('results');
    const fileNameDisplay = document.getElementById('filename-display');
    const previewBody = document.getElementById('preview-body');
    const uploadInfo = document.querySelector('.upload-info');
    const filePreview = document.getElementById('file-preview');
    const downloadBtn = document.getElementById('download-btn');

    let selectedFile = null;
    let outputFilename = null;

    // Drag and Drop
    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        handleFiles(e.dataTransfer.files);
    });

    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });

    function handleFiles(files) {
        if (files.length > 0) {
            const file = files[0];
            if (file.type === 'application/pdf') {
                selectedFile = file;
                fileNameDisplay.textContent = file.name;
                uploadInfo.classList.add('hidden');
                filePreview.classList.remove('hidden');
                extractBtn.disabled = false;
            } else {
                alert('Please upload a PDF file.');
            }
        }
    }

    // Extraction
    extractBtn.addEventListener('click', async () => {
        if (!selectedFile) return;

        extractBtn.disabled = true;
        statusDiv.classList.remove('hidden');
        resultsArea.classList.add('hidden');

        const formData = new FormData();
        formData.append('file', selectedFile);

        try {
            const response = await axios.post('/api/extract', formData);
            const data = response.data;

            outputFilename = data.output_file;
            document.getElementById('result-count').textContent = data.row_count;

            // Populate Preview Table
            previewBody.innerHTML = '';
            data.preview_data.forEach(row => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${row.INV_DATE || '-'}</td>
                    <td>${row.INV_NUMBER || '-'}</td>
                    <td>${row.BILLING_PERIOD || '-'}</td>
                    <td>${row.FIRSTNAME || '-'}</td>
                    <td>${row.LASTNAME || '-'}</td>
                    <td>${row.MEMBERID || '-'}</td>
                    <td>${row.PLAN_NAME || '-'}</td>
                    <td>${row.PLAN_TYPE || '-'}</td>
                    <td>${row.COVERAGE || '-'}</td>
                    <td>$${row.CURRENT_PREMIUM || '0.00'}</td>
                    <td>$${row.ADJUSTMENT_PREMIUM || '0.00'}</td>
                    <td>$${row.PRICING_ADJUSTMENT || '0.00'}</td>
                `;
                previewBody.appendChild(tr);
            });

            resultsArea.classList.remove('hidden');
            statusText.textContent = 'Extraction complete!';
            setTimeout(() => {
                statusDiv.classList.add('hidden');
            }, 3000);

        } catch (error) {
            console.error(error);
            const errorMsg = error.response?.data?.detail || error.message;

            // Show error in result count area
            document.getElementById('result-count').textContent = 'Error';
            document.querySelector('.success-badge').textContent = 'Failed';
            document.querySelector('.success-badge').style.backgroundColor = '#ef4444';

            alert('Extraction failed: ' + errorMsg);

            statusText.textContent = 'Extraction failed.';
            extractBtn.disabled = false;
            setTimeout(() => {
                statusDiv.classList.add('hidden');
            }, 5000);
        }
    });

    // Download
    downloadBtn.addEventListener('click', () => {
        if (outputFilename) {
            window.location.href = `/api/download/${outputFilename}`;
        }
    });
});
