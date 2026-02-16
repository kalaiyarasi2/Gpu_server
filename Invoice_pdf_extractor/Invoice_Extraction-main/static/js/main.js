document.addEventListener('DOMContentLoaded', () => {
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
                    <td>${row.POLICYID || '-'}</td>
                    <td>${row.MEMBERID || '-'}</td>
                    <td>${row.FIRSTNAME || '-'}</td>
                    <td>${row.LASTNAME || '-'}</td>
                    <td>$${row.BILLED_AMOUNT || '0.00'}</td>
                    <td>$${row.CURRENT_PREMIUM || '0.00'}</td>
                    <td>${row.Coverage || row.COVERAGE || '-'}</td>
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
            alert('Extraction failed: ' + (error.response?.data?.detail || error.message));
            extractBtn.disabled = false;
            statusDiv.classList.add('hidden');
        }
    });

    // Download
    downloadBtn.addEventListener('click', () => {
        if (outputFilename) {
            window.location.href = `/api/download/${outputFilename}`;
        }
    });
});
