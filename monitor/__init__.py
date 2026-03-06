"""
PDF Processing Monitor

A comprehensive monitoring system for tracking document processing requests
in the Unified PDF Platform. Provides real-time monitoring, statistics, and
a web-based dashboard for request tracking.

Features:
- Real-time request monitoring with detailed tracking
- SQLite database for persistent storage
- REST API endpoints for accessing monitoring data
- Web-based dashboard for visual monitoring
- Automatic cleanup of old records
- Thread-safe operations with comprehensive logging

Usage:
    from monitor import add_monitoring_to_app
    app = add_monitoring_to_app(app)
"""

from .monitor_db import monitor_db
from .service import request_monitor
from .middleware import add_monitoring_to_app, RequestMonitoringMiddleware
from .endpoints import router as monitor_router

__version__ = "1.0.0"
__author__ = "PDF Processing Monitor Team"

# Re-export key components for easy import
__all__ = [
    'monitor_db',
    'request_monitor', 
    'add_monitoring_to_app',
    'RequestMonitoringMiddleware',
    'monitor_router'
]