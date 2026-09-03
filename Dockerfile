

# Stage 1: Build the React Frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend

# Copy package files and install dependencies
COPY Unified_PDF_Platform/frontend/package*.json ./
RUN npm install

# Copy frontend source and build
COPY Unified_PDF_Platform/frontend/ ./
RUN npm run build

# Stage 2: Build the Python Backend
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    tesseract-ocr \
    poppler-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libmagic1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create and set the working directory
WORKDIR /app

# Copy the entire project
COPY . .

# Copy the built frontend from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/Unified_PDF_Platform/frontend/dist

# Install Python dependencies
# 1. Install Unified Platform requirements
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r Unified_PDF_Platform/requirements.txt

# 2. Install Email Pipeline requirements (if any)
RUN if [ -f Email_pipeline/requirements.txt ]; then pip install --no-cache-dir -r Email_pipeline/requirements.txt; fi

# 3. Install Root requirements (if any)
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# Create necessary directories for uploads and outputs
RUN mkdir -p Unified_PDF_Platform/uploads Unified_PDF_Platform/unified_outputs \
    && mkdir -p Insurance_pdf_extractor-main/backend/outputs \
    && mkdir -p work_compenstaion/backend/outputs \
    && mkdir -p outputs

# Expose the port the Unified App runs on
EXPOSE 8007

# Set the command to run the Unified Application
# We run from the root directory so imports work correctly
CMD ["python", "Unified_PDF_Platform/unified_app.py"]
