from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
import time
import logging
from typing import Callable, Optional, Tuple
import traceback
from pathlib import Path

from .service import request_monitor

logger = logging.getLogger('monitor_middleware')

def _extract_response_body(response: Response) -> Tuple[Response, Optional[bytes]]:
    """
    Best-effort capture of response body for logging/monitoring.
    If we have to consume a streaming iterator, we rebuild the response.
    """
    try:
        body = getattr(response, "body", None)
        if isinstance(body, (bytes, bytearray)):
            return response, bytes(body)
    except Exception:
        pass

    # Fallback for StreamingResponse-like objects
    body_iter = getattr(response, "body_iterator", None)
    if body_iter is None:
        return response, None

    async def _read_all() -> bytes:
        chunks = []
        async for chunk in body_iter:
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    # We are in async middleware; read + rebuild
    async def _consume_and_rebuild() -> Tuple[Response, bytes]:
        body_bytes = await _read_all()
        headers = dict(response.headers)
        rebuilt = Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=getattr(response, "media_type", None),
        )
        return rebuilt, body_bytes

    # Return a placeholder; caller will await the coroutine if needed
    return response, _consume_and_rebuild()  # type: ignore[return-value]


class RequestMonitoringMiddleware(BaseHTTPMiddleware):
    """Middleware to monitor all requests to the /api/extract endpoint."""
    
    # Endpoints that perform document extraction and should be monitored
    MONITORED_PATHS = {"/api/extract", "/cognethro", "/work-comp", "/bank-statement"}

    async def dispatch(self, request: Request, call_next: Callable):
        # Monitor all extraction endpoints
        if request.method == "POST" and request.url.path in self.MONITORED_PATHS:
            return await self.monitor_extract_request(request, call_next)
        
        # For other endpoints, proceed normally
        return await call_next(request)
    
    async def monitor_extract_request(self, request: Request, call_next: Callable):
        """Monitor the /api/extract endpoint specifically."""
        start_time = time.time()
        request_id = None
        # Important: do NOT read/parse multipart body here (it can break downstream file parsing).
        # We will start with placeholders and let the endpoint update file info via request_id.
        file_info = {"filename": "unknown", "file_size": 0}
        
        try:
            # Get source IP
            source_ip = request.client.host if request.client else "unknown"
            
            # Start monitoring
            request_id = request_monitor.start_request(
                filename=file_info["filename"],
                file_size=file_info["file_size"],
                source_ip=source_ip
            )
            
            logger.info(f"Monitoring started for request {request_id}: {file_info['filename']}")
            
            # Add request_id to request state for use in handlers
            request.state.monitoring_request_id = request_id
            
            # Proceed with the request
            response = await call_next(request)
            
            # Calculate processing time
            processing_time = time.time() - start_time

            # Attach request id to the response for easier client-side debugging
            try:
                response.headers["X-Monitor-Request-Id"] = str(request_id)
            except Exception:
                pass
            
            # Check if the response indicates success or failure
            if response.status_code == 200:
                # Try to get output files from response
                output_files = []
                try:
                    import json
                    resp_obj, body_or_coro = _extract_response_body(response)
                    if hasattr(body_or_coro, "__await__"):
                        resp_obj, body_bytes = await body_or_coro  # type: ignore[misc]
                        response = resp_obj
                    else:
                        body_bytes = body_or_coro

                    if body_bytes:
                        response_data = json.loads(body_bytes.decode(errors="replace"))
                        if isinstance(response_data, dict):
                            if 'excel' in response_data and response_data['excel']:
                                output_files.append(response_data['excel'])
                            if 'json' in response_data and response_data['json']:
                                output_files.append(response_data['json'])
                            
                            # Extract document_type and provider for database record
                            doc_type = response_data.get('type')
                            provider = response_data.get('provider') or response_data.get('insurer')
                            pages = response_data.get('pages', 0)
                            if doc_type or provider:
                                request_monitor.update_request_status(
                                    request_id=request_id,
                                    status='completed',
                                    document_type=doc_type,
                                    provider=provider
                                )
                except Exception:
                    pass
                
                # Complete the request successfully
                metadata = {}
                if 'pages' in locals() and pages:
                    metadata['pages'] = pages
                request_monitor.complete_request(
                    request_id=request_id,
                    output_files=output_files,
                    processing_time=processing_time,
                    metadata=metadata if metadata else None
                )
                
                logger.info(f"Request {request_id} completed successfully in {processing_time:.2f}s")
            else:
                # Request failed
                error_details = None
                try:
                    resp_obj, body_or_coro = _extract_response_body(response)
                    if hasattr(body_or_coro, "__await__"):
                        resp_obj, body_bytes = await body_or_coro  # type: ignore[misc]
                        response = resp_obj
                    else:
                        body_bytes = body_or_coro

                    if body_bytes:
                        text = body_bytes.decode(errors="replace").strip()
                        # Keep logs readable
                        if len(text) > 4000:
                            text = text[:4000] + "…(truncated)"
                        error_details = f"HTTP {response.status_code}: {text}"
                except Exception:
                    pass

                if not error_details:
                    error_details = f"HTTP {response.status_code}: Unknown error"
                request_monitor.fail_request(
                    request_id=request_id,
                    error_details=error_details,
                    processing_time=processing_time
                )
                
                logger.error(f"Request {request_id} failed: {error_details}")
            
            return response
            
        except Exception as e:
            # Handle any exceptions during monitoring
            processing_time = time.time() - start_time
            
            if request_id:
                error_details = f"{type(e).__name__}: {str(e)}"
                request_monitor.fail_request(
                    request_id=request_id,
                    error_details=error_details,
                    processing_time=processing_time
                )
                
                logger.error(f"Exception in monitoring for request {request_id}: {error_details}")
                logger.error(traceback.format_exc())
            
            # Return an error response
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Monitoring system error",
                    "details": str(e),
                    "request_id": request_id
                }
            )

class MonitoringIntegration:
    """Helper class to integrate monitoring with existing processing functions."""
    
    @staticmethod
    def wrap_processing_function(original_func):
        """Wrap an existing processing function with monitoring."""
        async def wrapped_function(*args, **kwargs):
            # Extract request from args or kwargs
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if not request:
                for value in kwargs.values():
                    if isinstance(value, Request):
                        request = value
                        break
            
            if not request:
                # If no request found, just call the original function
                return await original_func(*args, **kwargs)
            
            # Get request_id from request state
            request_id = getattr(request.state, 'monitoring_request_id', None)
            
            if not request_id:
                # If no monitoring is active, just call the original function
                return await original_func(*args, **kwargs)
            
            try:
                # Call the original function
                result = await original_func(*args, **kwargs)
                
                # Update monitoring status based on result
                if isinstance(result, dict) and "error" in result:
                    request_monitor.fail_request(
                        request_id=request_id,
                        error_details=result["error"]
                    )
                else:
                    # Success - we'll let the middleware handle the completion
                    pass
                
                return result
                
            except Exception as e:
                # Update monitoring with error
                request_monitor.fail_request(
                    request_id=request_id,
                    error_details=f"{type(e).__name__}: {str(e)}"
                )
                raise
        
        return wrapped_function

def add_monitoring_to_app(app):
    """Add monitoring middleware to a FastAPI application."""
    # Add the monitoring middleware
    app.add_middleware(RequestMonitoringMiddleware)
    
    logger.info("Monitoring middleware added to FastAPI application")
    
    return app

def get_monitoring_status(request_id: str) -> dict:
    """Get the current monitoring status for a request."""
    return request_monitor.get_request_status(request_id) or {}

def get_monitoring_statistics() -> dict:
    """Get monitoring statistics."""
    return request_monitor.get_statistics()

def get_active_monitoring_requests() -> list:
    """Get all currently active monitored requests."""
    return request_monitor.get_active_requests()