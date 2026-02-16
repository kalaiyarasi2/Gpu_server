
# PDF Invoice Extractor — Dark & Futuristic UI

## Overview
A sleek, dark-themed frontend for your existing FastAPI PDF Invoice Extractor. Features a dramatic file upload experience, step-by-step processing animation, and a completion popup with data preview and download.

---

## Pages & Flow

### 1. Home / Upload Page
- **Dark background** with subtle glowing grid or particle effects
- App title "PDF Invoice Extractor" with a neon/cyan accent glow
- **Drag & drop zone** with a pulsing border animation — users can drag a PDF or click to browse
- File type validation (PDF only) with instant feedback
- A single "Extract" button that triggers the upload

### 2. Processing Stage (Step-by-Step Progress)
Once the user clicks Extract, the upload zone transitions into a **multi-step progress view**:
- **Step 1: Uploading** — animated file icon flying upward, progress bar
- **Step 2: Analyzing PDF** — scanning/pulse animation on a document icon
- **Step 3: Extracting Data** — rows appearing one by one animation
- **Step 4: Complete** ✓ — green glow confirmation

Each step has a glowing indicator (idle → active → done), creating a futuristic pipeline feel. Steps animate sequentially as the API processes the file.

### 3. Completion Popup (Dialog)
When extraction finishes, a **modal popup** slides in with:
- Success icon with glow animation
- Summary: filename, number of rows extracted
- **Data preview table** showing the first 5 rows returned by the API
- **Download button** to get the Excel file (`/api/download/{filename}`)
- "Extract Another" button to reset and start over

---

## API Integration
- **POST `/api/extract`** — sends the PDF file, receives extraction results (row count, preview data, output filename)
- **GET `/api/download/{filename}`** — downloads the generated Excel file
- Configurable API base URL so users can point to their FastAPI server
- Error handling for quota errors (429) and general failures with styled error toasts

---

## Design Details
- Dark theme with cyan/blue neon accents
- Smooth transitions between upload → processing → complete states
- Glassmorphism cards with subtle backdrop blur
- All animations feel responsive and purposeful
