# PDF Processing Monitor

A comprehensive monitoring system for tracking document processing requests in the Unified PDF Platform.

## Features

- **Real-time Request Monitoring**: Track all document processing requests as they flow through the system
- **Detailed Request Tracking**: Monitor each processing step with timestamps and duration
- **SQLite Database**: Persistent storage with thread-safe operations
- **REST API Endpoints**: Access monitoring data programmatically
- **Web-based Dashboard**: Visual monitoring interface with real-time updates
- **Automatic Cleanup**: Configurable retention policies to prevent database bloat
- **Comprehensive Logging**: Detailed logs for troubleshooting and analysis

## Architecture

The monitoring system consists of several components:

### Core Components

1. **monitor_db.py** - Database layer with SQLite storage and thread-safe operations
2. **service.py** - Core monitoring service for tracking request lifecycle
3. **middleware.py** - FastAPI middleware for automatic request monitoring
4. **endpoints.py** - REST API endpoints for accessing monitoring data
5. **dashboard/** - Web-based dashboard for visual monitoring

### Data Tracked

For each request, the system tracks:

- **Request Metadata**: ID, filename, file size, source IP, timestamp
- **Processing Status**: pending → processing → completed/failed
- **Document Classification**: detected type, provider, confidence level
- **Processing Pipeline**: 7-layer progress with timestamps and duration
- **Performance Metrics**: total processing time, layer-specific times
- **Error Details**: specific error messages, stack traces
- **Output Information**: generated files, download URLs

## Installation

The monitoring system is designed to integrate seamlessly with your existing FastAPI application without requiring any changes to your current code.

### Prerequisites

- Python 3.7+
- FastAPI
- SQLite3 (included with Python)

### Integration

1. **Add monitoring to your FastAPI application**:

```python
from fastapi import FastAPI
from monitor import add_monitoring_to_app

app = FastAPI()

# Add monitoring middleware
app = add_monitoring_to_app(app)

# Include monitoring endpoints
from monitor.endpoints import router as monitor_router
app.include_router(monitor_router)
```

2. **Access the monitoring dashboard**:
   - Dashboard: `http://localhost:8007/monitor/dashboard`
   - API endpoints: `http://localhost:8007/api/monitor/*`

## API Endpoints

### Monitoring Statistics
```
GET /api/monitor/statistics
```
Returns overall monitoring statistics including success rates and performance metrics.

### Active Requests
```
GET /api/monitor/active
```
Returns all currently active requests being processed.

### Request History
```
GET /api/monitor/history?status=completed&limit=100
```
Returns request history with optional filtering by status.

### Request Details
```
GET /api/monitor/request/{request_id}
```
Returns detailed information about a specific request including all processing steps.

### Processing Steps
```
GET /api/monitor/steps/{request_id}
```
Returns all processing steps for a specific request.

### Recent Activity
```
GET /api/monitor/recent?hours=24
```
Returns recent activity within a specified time window.

### Error Summary
```
GET /api/monitor/errors?hours=24
```
Returns error summary and details for failed requests.

### Performance Metrics
```
GET /api/monitor/performance?hours=24
```
Returns performance metrics including processing times and throughput.

### Cleanup
```
POST /api/monitor/cleanup?days_to_keep=30
```
Cleans up old monitoring records to prevent database bloat.

### Health Check
```
GET /api/monitor/health
```
Health check endpoint for the monitoring system.

## Dashboard

The web-based dashboard provides a visual interface for monitoring:

- **Real-time Statistics**: Live updates of key metrics
- **Active Requests**: Current processing requests
- **Request History**: Recent processing history
- **Progress Visualization**: Processing progress bar
- **Error Tracking**: Failed requests and error details

Access the dashboard at: `http://localhost:8007/monitor/dashboard`

## Database Schema

### requests table
- `id`: Request ID (UUID)
- `filename`: Original filename
- `file_size`: File size in bytes
- `source_ip`: Client IP address
- `timestamp`: Request timestamp
- `status`: Request status (pending, processing, completed, failed)
- `document_type`: Detected document type
- `provider`: Detected provider/carrier
- `processing_time`: Total processing time in seconds
- `error_details`: Error message if failed
- `retry_count`: Number of retry attempts
- `output_files`: JSON array of output file paths
- `metadata`: JSON object for additional metadata

### processing_steps table
- `id`: Auto-incrementing ID
- `request_id`: Foreign key to requests table
- `step_name`: Name of processing step
- `step_status`: Step status (started, completed, failed)
- `start_time`: Step start timestamp
- `end_time`: Step end timestamp
- `duration`: Step duration in seconds
- `error_message`: Error message if step failed

## Configuration

### Database Location
By default, the database is stored at `monitor/requests.db`. You can specify a custom path:

```python
from monitor.monitor_db import MonitorDatabase
monitor_db = MonitorDatabase(db_path="custom/path/monitor.db")
```

### Cleanup Settings
Configure automatic cleanup of old records:

```python
# Clean up records older than 30 days
monitor_db.cleanup_old_records(days_to_keep=30)
```

### Logging
The monitoring system uses Python's logging module. Configure logging in your application:

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

## Usage Examples

### Programmatic Access

```python
from monitor.service import request_monitor

# Get monitoring statistics
stats = request_monitor.get_statistics()
print(f"Success rate: {stats['success_rate']}%")

# Get active requests
active = request_monitor.get_active_requests()
print(f"Active requests: {len(active)}")

# Get request details
request_data = request_monitor.get_request_status("request-id-here")
if request_data:
    print(f"Status: {request_data['status']}")
    steps = request_monitor.get_processing_steps("request-id-here")
    for step in steps:
        print(f"Step: {step['step_name']} - {step['step_status']}")
```

### Monitoring Specific Functions

```python
from monitor.service import monitor_request_processing

@monitor_request_processing
def process_document(request_id, document_path):
    # Your processing logic here
    pass
```

## Troubleshooting

### Common Issues

1. **Database Locked Errors**: The system uses thread-safe operations, but ensure proper error handling in high-concurrency scenarios.

2. **Missing Request Data**: Verify that the monitoring middleware is properly integrated and that requests are being processed through the `/api/extract` endpoint.

3. **Dashboard Not Loading**: Check that the monitoring endpoints are accessible and that the API base URL is correct.

### Logs

Check the monitoring logs for detailed information:

- **File**: `monitor/monitor.log`
- **Console**: Real-time logging to stdout

### Performance

- The monitoring system is designed to have minimal impact on processing performance
- Database operations are optimized with proper indexing
- In-memory caching is used for active requests
- Automatic cleanup prevents database bloat

## Security

- Request IDs are generated using UUID4 for security
- File paths are sanitized to prevent path traversal
- Error messages are sanitized to prevent information leakage
- Database access is controlled through proper permissions

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Support

For support and questions:
- Check the logs for error details
- Verify API endpoint accessibility
- Ensure proper integration with your FastAPI application