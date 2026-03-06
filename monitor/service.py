import uuid
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
import threading
import logging

from .monitor_db import monitor_db

logger = logging.getLogger('monitor_service')

class RequestMonitor:
    """Core monitoring service that tracks request lifecycle."""
    
    def __init__(self):
        self.active_requests: Dict[str, Dict] = {}  # In-memory cache for active requests
        self.lock = threading.Lock()
    
    def start_request(self, filename: str, file_size: int, source_ip: str) -> str:
        """Start monitoring a new request."""
        request_id = str(uuid.uuid4())
        
        with self.lock:
            self.active_requests[request_id] = {
                'id': request_id,
                'filename': filename,
                'file_size': file_size,
                'source_ip': source_ip,
                'start_time': datetime.now(),
                'status': 'pending',
                'steps': {}
            }
        
        # Create database record
        success = monitor_db.create_request(request_id, filename, file_size, source_ip)
        if success:
            monitor_db.add_processing_step(
                request_id, "request_received", "completed", 
                start_time=datetime.now()
            )
            logger.info(f"Started monitoring request {request_id}")
        
        return request_id

    def update_request_file_info(self, request_id: str, filename: Optional[str] = None, file_size: Optional[int] = None) -> bool:
        """Update filename / file_size after request starts (e.g., from endpoint)."""
        with self.lock:
            if request_id in self.active_requests:
                if filename:
                    self.active_requests[request_id]["filename"] = filename
                if file_size is not None:
                    self.active_requests[request_id]["file_size"] = file_size
        return monitor_db.update_request_file_info(request_id, filename=filename, file_size=file_size)
    
    def update_request_status(self, request_id: str, status: str, 
                            document_type: Optional[str] = None,
                            provider: Optional[str] = None,
                            error_details: Optional[str] = None) -> bool:
        """Update request status."""
        with self.lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['status'] = status
                if document_type:
                    self.active_requests[request_id]['document_type'] = document_type
                if provider:
                    self.active_requests[request_id]['provider'] = provider
        
        # Update database
        return monitor_db.update_request_status(
            request_id, status, document_type, provider, None, error_details
        )
    
    def start_processing_step(self, request_id: str, step_name: str) -> bool:
        """Start a processing step."""
        start_time = datetime.now()
        
        with self.lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['steps'][step_name] = {
                    'name': step_name,
                    'status': 'started',
                    'start_time': start_time
                }
        
        return monitor_db.add_processing_step(
            request_id, step_name, "started", start_time=start_time
        )
    
    def complete_processing_step(self, request_id: str, step_name: str, 
                               error_message: Optional[str] = None) -> bool:
        """Complete a processing step."""
        end_time = datetime.now()
        
        with self.lock:
            if request_id in self.active_requests and step_name in self.active_requests[request_id]['steps']:
                step = self.active_requests[request_id]['steps'][step_name]
                step['status'] = 'completed' if not error_message else 'failed'
                step['end_time'] = end_time
                
                if 'start_time' in step:
                    duration = (end_time - step['start_time']).total_seconds()
                    step['duration'] = duration
        
        duration = None
        with self.lock:
            if request_id in self.active_requests and step_name in self.active_requests[request_id]['steps']:
                step = self.active_requests[request_id]['steps'][step_name]
                if 'duration' in step:
                    duration = step['duration']
        
        return monitor_db.update_processing_step(
            request_id, step_name, "completed" if not error_message else "failed",
            end_time=end_time, duration=duration, error_message=error_message
        )
    
    def complete_request(self, request_id: str, output_files: List[str], 
                       processing_time: Optional[float] = None,
                       metadata: Optional[Dict] = None) -> bool:
        """Complete a request successfully."""
        end_time = datetime.now()
        
        with self.lock:
            if request_id in self.active_requests:
                request_data = self.active_requests[request_id]
                start_time = request_data['start_time']
                
                if processing_time is None:
                    processing_time = (end_time - start_time).total_seconds()
                
                request_data['status'] = 'completed'
                request_data['processing_time'] = processing_time
                request_data['output_files'] = output_files
                request_data['end_time'] = end_time
        
        # Update database
        success1 = monitor_db.update_request_status(
            request_id, 'completed', processing_time=processing_time
        )
        success2 = monitor_db.add_output_files(request_id, output_files)
        
        if metadata:
            success3 = monitor_db.add_metadata(request_id, metadata)
        else:
            success3 = True
        
        # Remove from active requests
        with self.lock:
            if request_id in self.active_requests:
                del self.active_requests[request_id]
        
        if success1 and success2 and success3:
            logger.info(f"Completed monitoring for request {request_id} in {processing_time:.2f}s")
            return True
        return False
    
    def fail_request(self, request_id: str, error_details: str, 
                   processing_time: Optional[float] = None) -> bool:
        """Mark a request as failed."""
        end_time = datetime.now()
        
        with self.lock:
            if request_id in self.active_requests:
                request_data = self.active_requests[request_id]
                start_time = request_data['start_time']
                
                if processing_time is None:
                    processing_time = (end_time - start_time).total_seconds()
                
                request_data['status'] = 'failed'
                request_data['processing_time'] = processing_time
                request_data['error_details'] = error_details
                request_data['end_time'] = end_time
        
        # Update database
        success = monitor_db.update_request_status(
            request_id, 'failed', processing_time=processing_time, error_details=error_details
        )
        
        # Remove from active requests
        with self.lock:
            if request_id in self.active_requests:
                del self.active_requests[request_id]
        
        if success:
            logger.error(f"Failed monitoring for request {request_id}: {error_details}")
            return True
        return False
    
    def get_request_status(self, request_id: str) -> Optional[Dict]:
        """Get current status of a request."""
        # Check active requests first
        with self.lock:
            if request_id in self.active_requests:
                return self.active_requests[request_id].copy()
        
        # Check database
        return monitor_db.get_request(request_id)
    
    def get_active_requests(self) -> List[Dict]:
        """Get all active requests."""
        with self.lock:
            return list(self.active_requests.values())
    
    def get_request_history(self, limit: int = 100, status: Optional[str] = None) -> List[Dict]:
        """Get request history from database."""
        return monitor_db.get_requests(status=status, limit=limit)
    
    def get_processing_steps(self, request_id: str) -> List[Dict]:
        """Get processing steps for a request."""
        return monitor_db.get_processing_steps(request_id)
    
    def get_statistics(self) -> Dict:
        """Get monitoring statistics."""
        return monitor_db.get_statistics()
    
    def cleanup_old_records(self, days_to_keep: int = 30) -> bool:
        """Clean up old records."""
        return monitor_db.cleanup_old_records(days_to_keep)

# Global monitor instance
request_monitor = RequestMonitor()

class MonitoringContext:
    """Context manager for monitoring a request processing pipeline."""
    
    def __init__(self, monitor: RequestMonitor, request_id: str, step_name: str):
        self.monitor = monitor
        self.request_id = request_id
        self.step_name = step_name
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        self.monitor.start_processing_step(self.request_id, self.step_name)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()
        
        if exc_type is None:
            self.monitor.complete_processing_step(self.request_id, self.step_name)
        else:
            error_msg = f"{exc_type.__name__}: {str(exc_val)}"
            self.monitor.complete_processing_step(self.request_id, self.step_name, error_message=error_msg)
            
            # Log the full traceback
            logger.error(f"Error in step '{self.step_name}' for request {self.request_id}:")
            logger.error(traceback.format_exc())

def monitor_step(step_name: str):
    """Decorator to monitor a processing step."""
    def decorator(func: Callable):
        def wrapper(monitor: RequestMonitor, request_id: str, *args, **kwargs):
            with MonitoringContext(monitor, request_id, step_name):
                return func(monitor, request_id, *args, **kwargs)
        return wrapper
    return decorator

def monitor_request_processing(func: Callable):
    """Decorator to monitor entire request processing."""
    def wrapper(*args, **kwargs):
        # Extract request_id from kwargs or args
        request_id = None
        if 'request_id' in kwargs:
            request_id = kwargs['request_id']
        elif len(args) > 1:
            request_id = args[1]  # Assuming request_id is second argument
        
        if request_id:
            logger.info(f"Starting monitored processing for request {request_id}")
        
        try:
            result = func(*args, **kwargs)
            if request_id:
                logger.info(f"Completed monitored processing for request {request_id}")
            return result
        except Exception as e:
            if request_id:
                logger.error(f"Error in monitored processing for request {request_id}: {e}")
                logger.error(traceback.format_exc())
            raise
    return wrapper