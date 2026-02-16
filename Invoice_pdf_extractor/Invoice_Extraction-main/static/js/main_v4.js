document.addEventListener('DOMContentLoaded', () => {
    // --- Theme Toggle Logic ---
    const themeToggle = document.getElementById('theme-toggle');
    const themeIcon = themeToggle.querySelector('i');
    const body = document.body;

    const savedTheme = localStorage.getItem('invoice-ai-theme') || 'light';
    setTheme(savedTheme);

    themeToggle.addEventListener('click', () => {
        const currentTheme = body.classList.contains('dark-mode') ? 'dark' : 'light';
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        setTheme(newTheme);
    });

    function setTheme(theme) {
        if (theme === 'dark') {
            body.classList.add('dark-mode');
            themeIcon.classList.remove('fa-moon');
            themeIcon.classList.add('fa-sun');
        } else {
            body.classList.remove('dark-mode');
            themeIcon.classList.remove('fa-sun');
            themeIcon.classList.add('fa-moon');
        }
        localStorage.setItem('invoice-ai-theme', theme);
    }

    // --- UI Elements ---
    const uploadView = document.getElementById('upload-view');
    const dashboardMain = document.getElementById('dashboard-main');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const browseBtn = document.getElementById('browse-btn');
    const queueList = document.getElementById('queue-list');
    const uploadMoreBtn = document.getElementById('upload-more-btn');

    const loadingAnimation = document.getElementById('loading-animation');
    const completionView = document.getElementById('completion-view');
    const currentFilenameDisplay = document.getElementById('current-filename');
    const extractionSummary = document.getElementById('extraction-summary');
    const downloadBtn = document.getElementById('download-btn');
    const downloadJsonBtn = document.getElementById('download-json-btn');

    let processingQueue = [];
    let isCurrentlyProcessing = false;
    let currentOutputFilename = null;
    let currentJsonFilename = null;

    // --- Event Listeners ---
    browseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });

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

    uploadMoreBtn.addEventListener('click', () => {
        fileInput.click();
    });

    // --- Core Logic ---
    function handleFiles(files) {
        if (files.length === 0) return;

        // Show Dashboard Main Layout
        uploadView.classList.add('hidden');
        dashboardMain.classList.remove('hidden');

        Array.from(files).forEach(file => {
            if (file.type !== 'application/pdf') {
                alert(`File ${file.name} is not a PDF.`);
                return;
            }
            const fileObj = {
                id: 'file-' + Date.now() + '-' + Math.floor(Math.random() * 1000),
                file: file,
                status: 'pending'
            };
            processingQueue.push(fileObj);
            addToFileQueueUI(fileObj);
        });

        processNextInQueue();
    }

    function addToFileQueueUI(fileObj) {
        const item = document.createElement('div');
        item.className = 'queue-item';
        item.id = fileObj.id;
        item.innerHTML = `
            <div class="file-info-group">
                <span class="file-name">${fileObj.file.name}</span>
                <span class="file-size">${(fileObj.file.size / 1024 / 1024).toFixed(2)} MB</span>
            </div>
            <span class="status-badge status-pending">Pending</span>
        `;
        queueList.appendChild(item);
    }

    async function processNextInQueue() {
        if (isCurrentlyProcessing) return;

        const nextFile = processingQueue.find(f => f.status === 'pending');
        if (!nextFile) return;

        isCurrentlyProcessing = true;
        nextFile.status = 'processing';
        updateQueueItemUI(nextFile, 'processing');

        try {
            await runExtraction(nextFile);
            nextFile.status = 'complete';
            updateQueueItemUI(nextFile, 'complete');
        } catch (err) {
            console.error(err);
            nextFile.status = 'error';
            updateQueueItemUI(nextFile, 'error');
        }

        isCurrentlyProcessing = false;
        processNextInQueue();
    }

    function updateQueueItemUI(fileObj, status) {
        const item = document.getElementById(fileObj.id);
        if (!item) return;
        const badge = item.querySelector('.status-badge');

        item.classList.remove('active-item');
        badge.className = 'status-badge';

        if (status === 'processing') {
            item.classList.add('active-item');
            badge.textContent = 'Processing';
            badge.classList.add('status-processing');
        } else if (status === 'complete') {
            badge.textContent = 'Complete';
            badge.classList.add('status-complete');
        } else if (status === 'error') {
            badge.textContent = 'Error';
            badge.style.background = '#fee2e2';
            badge.style.color = '#ef4444';
        }
    }

    async function runExtraction(fileObj) {
        // Prepare UI for new extraction
        loadingAnimation.classList.remove('hidden');
        completionView.classList.add('hidden');
        currentFilenameDisplay.textContent = fileObj.file.name;
        resetSteps();

        try {
            // STEP 0: Checking Rotation
            updateStep(0, 'active');
            await delay(1000);
            updateStep(0, 'done');

            // STEP 1: Extracting Text
            updateStep(1, 'active');
            const formData = new FormData();
            formData.append('file', fileObj.file);
            const responsePromise = axios.post('/api/extract', formData);

            await delay(1500);
            updateStep(1, 'done');

            // STEP 2: Schema Extraction
            updateStep(2, 'active');
            const response = await responsePromise;
            const data = response.data;
            await delay(1200);
            updateStep(2, 'done');

            // STEP 3: Validating
            updateStep(3, 'active');
            await delay(800);
            updateStep(3, 'done');

            // STEP 4: Complete
            updateStep(4, 'done');
            currentOutputFilename = data.output_file;
            currentJsonFilename = data.output_json;
            extractionSummary.textContent = `Successfully extracted ${data.row_count} rows.`;

            // Show result
            loadingAnimation.classList.add('hidden');
            completionView.classList.remove('hidden');

        } catch (error) {
            const errorMsg = error.response?.data?.detail || error.message;
            throw new Error(errorMsg);
        }
    }

    // --- UI Helpers ---
    function updateStep(index, status) {
        const step = document.getElementById(`step-${index}`);
        if (!step) return;
        const iconContainer = step.querySelector('.step-icon');

        step.classList.remove('active', 'done');
        if (status === 'active') {
            step.classList.add('active');
            iconContainer.innerHTML = '<i class="fa-solid fa-sync fa-spin"></i>';
        } else if (status === 'done') {
            step.classList.add('done');
            iconContainer.innerHTML = '<i class="fa-solid fa-check"></i>';
        }
    }

    function resetSteps() {
        const icons = ['fa-sync', 'fa-file-word', 'fa-microchip', 'fa-database', 'fa-check-circle'];
        icons.forEach((icon, i) => {
            const step = document.getElementById(`step-${i}`);
            if (step) {
                step.classList.remove('active', 'done');
                step.querySelector('.step-icon').innerHTML = `<i class="fa-solid ${icon}"></i>`;
            }
        });
    }

    function delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    downloadBtn.addEventListener('click', () => {
        if (currentOutputFilename) {
            window.location.href = `/api/download/${currentOutputFilename}`;
        }
    });

    downloadJsonBtn.addEventListener('click', () => {
        if (currentJsonFilename) {
            window.location.href = `/api/download/${currentJsonFilename}`;
        }
    });
});
