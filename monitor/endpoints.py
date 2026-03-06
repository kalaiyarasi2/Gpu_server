from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import logging

from .service import request_monitor

logger = logging.getLogger('monitor_endpoints')

router = APIRouter(prefix="/api/monitor", tags=["Monitoring"])

@router.get("/statistics")
async def get_monitoring_statistics():
    """Get overall monitoring statistics."""
    try:
        stats = request_monitor.get_statistics()
        return {
            "success": True,
            "data": stats,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/active")
async def get_active_requests():
    """Get all currently active requests."""
    try:
        active_requests = request_monitor.get_active_requests()
        return {
            "success": True,
            "data": active_requests,
            "count": len(active_requests),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting active requests: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_request_history(
    status: Optional[str] = Query(None, description="Filter by status (pending, processing, completed, failed)"),
    limit: int = Query(100, ge=1, le=1000, description="Number of records to return")
):
    """Get request history with optional status filter."""
    try:
        history = request_monitor.get_request_history(limit=limit, status=status)
        return {
            "success": True,
            "data": history,
            "count": len(history),
            "filtered_by_status": status,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting request history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/request/{request_id}")
async def get_request_details(request_id: str):
    """Get detailed information about a specific request."""
    try:
        request_data = request_monitor.get_request_status(request_id)
        if not request_data:
            raise HTTPException(status_code=404, detail="Request not found")
        
        # Get processing steps
        steps = request_monitor.get_processing_steps(request_id)
        
        return {
            "success": True,
            "data": {
                "request": request_data,
                "steps": steps
            },
            "timestamp": datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting request details for {request_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/steps/{request_id}")
async def get_request_steps(request_id: str):
    """Get all processing steps for a specific request."""
    try:
        steps = request_monitor.get_processing_steps(request_id)
        return {
            "success": True,
            "data": steps,
            "count": len(steps),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting processing steps for {request_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/recent")
async def get_recent_activity(hours: int = Query(24, ge=1, le=168, description="Number of hours to look back")):
    """Get recent activity within the specified time window."""
    try:
        # Get all requests from the last N hours
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        # For now, get recent requests and filter by timestamp
        # In a real implementation, we'd add this to the database query
        recent_requests = request_monitor.get_request_history(limit=1000)
        
        filtered_requests = []
        for req in recent_requests:
            if 'timestamp' in req:
                req_time = datetime.fromisoformat(req['timestamp'].replace('Z', '+00:00'))
                if req_time >= cutoff_time:
                    filtered_requests.append(req)
        
        # Calculate summary statistics for the period
        total_requests = len(filtered_requests)
        completed_requests = len([r for r in filtered_requests if r.get('status') == 'completed'])
        failed_requests = len([r for r in filtered_requests if r.get('status') == 'failed'])
        processing_requests = len([r for r in filtered_requests if r.get('status') == 'processing'])
        
        success_rate = (completed_requests / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "success": True,
            "data": {
                "recent_requests": filtered_requests,
                "summary": {
                    "total_requests": total_requests,
                    "completed": completed_requests,
                    "failed": failed_requests,
                    "processing": processing_requests,
                    "success_rate": round(success_rate, 2),
                    "time_window_hours": hours
                }
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting recent activity: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/errors")
async def get_error_summary(hours: int = Query(24, ge=1, le=168)):
    """Get error summary and details for failed requests."""
    try:
        # Get recent requests and filter for errors
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_requests = request_monitor.get_request_history(limit=1000)
        
        failed_requests = []
        error_types = {}
        
        for req in recent_requests:
            if 'timestamp' in req and 'status' in req:
                req_time = datetime.fromisoformat(req['timestamp'].replace('Z', '+00:00'))
                if req_time >= cutoff_time and req['status'] == 'failed':
                    failed_requests.append(req)
                    
                    # Categorize errors
                    error_details = req.get('error_details', 'Unknown Error')
                    if error_details in error_types:
                        error_types[error_details] += 1
                    else:
                        error_types[error_details] = 1
        
        # Sort error types by frequency
        sorted_errors = sorted(error_types.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "success": True,
            "data": {
                "failed_requests": failed_requests,
                "error_summary": {
                    "total_failed": len(failed_requests),
                    "error_types": sorted_errors,
                    "time_window_hours": hours
                }
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting error summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/performance")
async def get_performance_metrics(hours: int = Query(24, ge=1, le=168)):
    """Get performance metrics including processing times and throughput."""
    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_requests = request_monitor.get_request_history(limit=1000)
        
        completed_requests = []
        processing_times = []
        
        for req in recent_requests:
            if 'timestamp' in req and 'status' in req and 'processing_time' in req:
                req_time = datetime.fromisoformat(req['timestamp'].replace('Z', '+00:00'))
                if req_time >= cutoff_time and req['status'] == 'completed':
                    completed_requests.append(req)
                    processing_times.append(req['processing_time'])
        
        # Calculate metrics
        avg_processing_time = sum(processing_times) / len(processing_times) if processing_times else 0
        min_processing_time = min(processing_times) if processing_times else 0
        max_processing_time = max(processing_times) if processing_times else 0
        
        # Calculate throughput (requests per hour)
        total_hours = hours
        throughput = len(completed_requests) / total_hours if total_hours > 0 else 0
        
        # Group by document type
        doc_type_stats = {}
        for req in completed_requests:
            doc_type = req.get('document_type', 'Unknown')
            if doc_type in doc_type_stats:
                doc_type_stats[doc_type]['count'] += 1
                doc_type_stats[doc_type]['times'].append(req['processing_time'])
            else:
                doc_type_stats[doc_type] = {
                    'count': 1,
                    'times': [req['processing_time']]
                }
        
        # Calculate average times per document type
        for doc_type, data in doc_type_stats.items():
            data['avg_time'] = sum(data['times']) / len(data['times'])
            data['min_time'] = min(data['times'])
            data['max_time'] = max(data['times'])
            del data['times']  # Remove raw times for cleaner output
        
        return {
            "success": True,
            "data": {
                "summary": {
                    "total_completed": len(completed_requests),
                    "avg_processing_time": round(avg_processing_time, 2),
                    "min_processing_time": round(min_processing_time, 2),
                    "max_processing_time": round(max_processing_time, 2),
                    "throughput_per_hour": round(throughput, 2),
                    "time_window_hours": hours
                },
                "by_document_type": doc_type_stats
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting performance metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cleanup")
async def cleanup_old_records(days_to_keep: int = Query(30, ge=7, le=365)):
    """Clean up old monitoring records to prevent database bloat."""
    try:
        success = request_monitor.cleanup_old_records(days_to_keep)
        if success:
            return {
                "success": True,
                "message": f"Cleaned up records older than {days_to_keep} days",
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=500, detail="Cleanup operation failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def monitor_health_check():
    """Health check endpoint for the monitoring system."""
    try:
        # Get basic statistics to verify database is working
        stats = request_monitor.get_statistics()
        
        # Check if we can access active requests
        active_count = len(request_monitor.get_active_requests())
        
        return {
            "success": True,
            "data": {
                "status": "healthy",
                "database_accessible": True,
                "active_requests": active_count,
                "total_requests": stats.get('total_requests', 0),
                "timestamp": datetime.now().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Monitor health check failed: {e}")
        return {
            "success": False,
            "data": {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        }