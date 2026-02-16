"""
Configuration file for Insurance Form Extractor
Customize settings here rather than modifying core files
"""

import os

class Config:
    """Main configuration class"""
    
    # API Settings
    FLASK_HOST = '0.0.0.0'
    FLASK_PORT = 5000
    FLASK_DEBUG = True
    
    # File Upload Settings
    MAX_FILE_SIZE_MB = 50
    ALLOWED_EXTENSIONS = {'pdf'}
    UPLOAD_FOLDER = '/tmp/insurance_uploads'
    
    # OCR Settings
    OCR_DPI = 300  # Resolution for PDF to image conversion
    OCR_ENGINE = 'deepseek'  # Options: 'deepseek', 'tesseract', 'auto'
    FALLBACK_OCR = True  # Use fallback OCR if primary fails
    
    # AI Model Settings
    USE_LOCAL_MODELS = False  # Set to True to run models locally
    HF_API_TOKEN = os.getenv('HF_API_TOKEN', '')
    
    # Model Endpoints
    DEEPSEEK_OCR_URL = 'https://api-inference.huggingface.co/models/deepseek-ai/deepseek-ocr'
    NUMARKDOWN_URL = 'https://api-inference.huggingface.co/models/numind/NuMarkdown-8B-Thinking'
    
    # Extraction Settings
    EXTRACTION_CONFIDENCE_THRESHOLD = 0.7  # Minimum confidence for accepting extracted data
    MAX_RETRY_ATTEMPTS = 2  # Retry extraction if confidence is low
    
    # Processing Settings
    ENABLE_ORIENTATION_DETECTION = True
    ENABLE_IMAGE_ENHANCEMENT = True
    PARALLEL_PAGE_PROCESSING = False  # Set to True for faster multi-page processing
    
    # Output Settings
    OUTPUT_FOLDER = '/mnt/user-data/outputs'
    INCLUDE_FULL_TEXT = True  # Include OCR text in metadata
    INCLUDE_CONFIDENCE_SCORES = True
    
    # Field Mapping (customize field names if your forms use different labels)
    FIELD_ALIASES = {
        'employee_name': ['Employee Name', 'Claimant', 'Name', 'Worker Name'],
        'claim_number': ['Claim Number', 'Claim #', 'File Number', 'File #', 'Case #'],
        'injury_date_time': ['Date of Injury', 'DOI', 'Injury Date', 'Accident Date', 'Date of Loss'],
        'status': ['Status', 'Claim Status', 'Current Status'],
        'body_part': ['Body Part', 'Part of Body', 'Body Part Affected', 'Injured Body Part'],
        'injury_type': ['Injury Type', 'Nature of Injury', 'Type of Injury', 'Nature'],
        'claim_class': ['Class', 'Claim Class', 'Classification', 'Type'],
        'injury_description': ['Description', 'Injury Description', 'Details', 'Accident Description'],
    }
    
    # Currency Fields (for proper formatting)
    CURRENCY_FIELDS = [
        'medical_paid',
        'medical_reserve',
        'indemnity_paid',
        'indemnity_reserve',
        'expense_paid',
        'expense_reserve',
        'recovery',
        'deductible',
        'total_incurred'
    ]
    
    # Validation Rules
    VALIDATION_RULES = {
        'claim_number': {
            'required': True,
            'pattern': r'^[A-Z0-9\-]+$',
            'min_length': 5,
            'max_length': 50
        },
        'injury_date_time': {
            'required': True,
            'format': 'date'
        },
        'medical_paid': {
            'required': False,
            'min_value': 0,
            'max_value': 10000000
        },
        'total_incurred': {
            'required': False,
            'min_value': 0,
            'max_value': 50000000
        }
    }
    
    # Logging
    LOG_LEVEL = 'INFO'  # Options: DEBUG, INFO, WARNING, ERROR
    LOG_FILE = 'insurance_extractor.log'
    ENABLE_CONSOLE_LOGGING = True
    
    # Performance
    CACHE_OCR_RESULTS = True
    CACHE_DURATION_SECONDS = 3600  # 1 hour
    
    @classmethod
    def validate(cls):
        """Validate configuration settings"""
        if cls.USE_LOCAL_MODELS and not cls.HF_API_TOKEN:
            print("Warning: HF_API_TOKEN not set. API calls may fail.")
        
        if cls.OCR_DPI < 150:
            print("Warning: OCR_DPI is set low. Quality may be affected.")
        
        # Create necessary directories
        os.makedirs(cls.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(cls.OUTPUT_FOLDER, exist_ok=True)
        
        return True


# Development Configuration
class DevelopmentConfig(Config):
    FLASK_DEBUG = True
    LOG_LEVEL = 'DEBUG'
    MAX_FILE_SIZE_MB = 100  # More lenient for testing


# Production Configuration
class ProductionConfig(Config):
    FLASK_DEBUG = False
    LOG_LEVEL = 'WARNING'
    USE_LOCAL_MODELS = True  # Recommended for production
    PARALLEL_PAGE_PROCESSING = True
    CACHE_OCR_RESULTS = True


# Testing Configuration
class TestingConfig(Config):
    FLASK_DEBUG = True
    LOG_LEVEL = 'DEBUG'
    MAX_FILE_SIZE_MB = 10
    EXTRACTION_CONFIDENCE_THRESHOLD = 0.5  # Lower threshold for testing


# Select configuration based on environment
ENV = os.getenv('FLASK_ENV', 'development')

if ENV == 'production':
    config = ProductionConfig
elif ENV == 'testing':
    config = TestingConfig
else:
    config = DevelopmentConfig

# Validate configuration on import
config.validate()