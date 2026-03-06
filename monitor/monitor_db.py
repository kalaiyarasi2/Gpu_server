import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

# Configure logging for the monitor
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('monitor/monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('monitor_db')

class MonitorDatabase:
    """Database manager for request monitoring with thread-safe operations."""
    
    def __init__(self, db_path: str = "monitor/requests.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.init_database()
    
    def init_database(self):
        """Initialize the database schema."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Main requests table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_size INTEGER,
                    source_ip TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    document_type TEXT,
                    provider TEXT,
                    processing_time REAL,
                    error_details TEXT,
                    retry_count INTEGER DEFAULT 0,
                    output_files TEXT,  -- JSON array of output file paths
                    metadata TEXT        -- JSON object for additional metadata
                )
            ''')
            
            # Processing steps table for detailed tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processing_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    step_name TEXT NOT NULL,
                    step_status TEXT NOT NULL,
                    start_time DATETIME,
                    end_time DATETIME,
                    duration REAL,
                    error_message TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            # Indexes for performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_steps_request_id ON processing_steps(request_id)')
            
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
    
    def create_request(self, request_id: str, filename: str, file_size: int, source_ip: str) -> bool:
        """Create a new request record."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO requests (id, filename, file_size, source_ip)
                    VALUES (?, ?, ?, ?)
                ''', (request_id, filename, file_size, source_ip))
                
                conn.commit()
                conn.close()
                logger.info(f"Created request: {request_id} - {filename}")
                return True
            except Exception as e:
                logger.error(f"Failed to create request {request_id}: {e}")
                return False
    
    def update_request_status(self, request_id: str, status: str, document_type: Optional[str] = None, 
                             provider: Optional[str] = None, processing_time: Optional[float] = None,
                             error_details: Optional[str] = None) -> bool:
        """Update request status and metadata."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                update_fields = []
                params = []
                
                if status:
                    update_fields.append("status = ?")
                    params.append(status)
                
                if document_type:
                    update_fields.append("document_type = ?")
                    params.append(document_type)
                
                if provider:
                    update_fields.append("provider = ?")
                    params.append(provider)
                
                if processing_time is not None:
                    update_fields.append("processing_time = ?")
                    params.append(processing_time)
                
                if error_details:
                    update_fields.append("error_details = ?")
                    params.append(error_details)
                
                if update_fields:
                    params.append(request_id)
                    query = f"UPDATE requests SET {', '.join(update_fields)} WHERE id = ?"
                    cursor.execute(query, params)
                
                conn.commit()
                conn.close()
                logger.info(f"Updated request {request_id} status to: {status}")
                return True
            except Exception as e:
                logger.error(f"Failed to update request {request_id}: {e}")
                return False

    def update_request_file_info(self, request_id: str, filename: Optional[str] = None, file_size: Optional[int] = None) -> bool:
        """Update filename and/or file_size for a request."""
        if not filename and file_size is None:
            return True
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                update_fields = []
                params = []

                if filename:
                    update_fields.append("filename = ?")
                    params.append(filename)

                if file_size is not None:
                    update_fields.append("file_size = ?")
                    params.append(file_size)

                params.append(request_id)
                query = f"UPDATE requests SET {', '.join(update_fields)} WHERE id = ?"
                cursor.execute(query, params)

                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error(f"Failed to update request file info {request_id}: {e}")
                return False
    
    def add_processing_step(self, request_id: str, step_name: str, step_status: str, 
                          start_time: Optional[datetime] = None, 
                          end_time: Optional[datetime] = None,
                          duration: Optional[float] = None,
                          error_message: Optional[str] = None) -> bool:
        """Add a processing step record."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO processing_steps 
                    (request_id, step_name, step_status, start_time, end_time, duration, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (request_id, step_name, step_status, start_time, end_time, duration, error_message))
                
                conn.commit()
                conn.close()
                logger.debug(f"Added step {step_name} for request {request_id}: {step_status}")
                return True
            except Exception as e:
                logger.error(f"Failed to add processing step for {request_id}: {e}")
                return False
    
    def update_processing_step(self, request_id: str, step_name: str, step_status: str,
                              end_time: Optional[datetime] = None,
                              duration: Optional[float] = None,
                              error_message: Optional[str] = None) -> bool:
        """Update an existing processing step."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                update_fields = []
                params = []
                
                update_fields.append("step_status = ?")
                params.append(step_status)
                
                if end_time:
                    update_fields.append("end_time = ?")
                    params.append(end_time)
                
                if duration is not None:
                    update_fields.append("duration = ?")
                    params.append(duration)
                
                if error_message:
                    update_fields.append("error_message = ?")
                    params.append(error_message)
                
                params.extend([request_id, step_name])
                
                query = f"UPDATE processing_steps SET {', '.join(update_fields)} WHERE request_id = ? AND step_name = ?"
                cursor.execute(query, params)
                
                conn.commit()
                conn.close()
                logger.debug(f"Updated step {step_name} for request {request_id}: {step_status}")
                return True
            except Exception as e:
                logger.error(f"Failed to update processing step {step_name} for {request_id}: {e}")
                return False
    
    def add_output_files(self, request_id: str, output_files: List[str]) -> bool:
        """Add output file paths to a request."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE requests SET output_files = ? WHERE id = ?
                ''', (json.dumps(output_files), request_id))
                
                conn.commit()
                conn.close()
                logger.info(f"Added output files for request {request_id}: {output_files}")
                return True
            except Exception as e:
                logger.error(f"Failed to add output files for {request_id}: {e}")
                return False
    
    def add_metadata(self, request_id: str, metadata: Dict[str, Any]) -> bool:
        """Add additional metadata to a request."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Get existing metadata
                cursor.execute('SELECT metadata FROM requests WHERE id = ?', (request_id,))
                result = cursor.fetchone()
                
                existing_metadata = {}
                if result and result[0]:
                    existing_metadata = json.loads(result[0])
                
                # Merge with new metadata
                existing_metadata.update(metadata)
                
                cursor.execute('''
                    UPDATE requests SET metadata = ? WHERE id = ?
                ''', (json.dumps(existing_metadata), request_id))
                
                conn.commit()
                conn.close()
                logger.info(f"Updated metadata for request {request_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to add metadata for {request_id}: {e}")
                return False
    
    def get_request(self, request_id: str) -> Optional[Dict]:
        """Get a single request by ID."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                
                if row:
                    columns = [description[0] for description in cursor.description]
                    request_data = dict(zip(columns, row))
                    
                    # Parse JSON fields
                    if request_data.get('output_files'):
                        request_data['output_files'] = json.loads(request_data['output_files'])
                    if request_data.get('metadata'):
                        request_data['metadata'] = json.loads(request_data['metadata'])
                    
                    conn.close()
                    return request_data
                conn.close()
                return None
            except Exception as e:
                logger.error(f"Failed to get request {request_id}: {e}")
                return None
    
    def get_requests(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get requests with optional status filter."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                query = "SELECT * FROM requests"
                params = []
                
                if status:
                    query += " WHERE status = ?"
                    params.append(status)
                
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                columns = [description[0] for description in cursor.description]
                requests = []
                
                for row in rows:
                    request_data = dict(zip(columns, row))
                    
                    # Parse JSON fields
                    if request_data.get('output_files'):
                        request_data['output_files'] = json.loads(request_data['output_files'])
                    if request_data.get('metadata'):
                        request_data['metadata'] = json.loads(request_data['metadata'])
                    
                    requests.append(request_data)
                
                conn.close()
                return requests
            except Exception as e:
                logger.error(f"Failed to get requests: {e}")
                return []
    
    def get_processing_steps(self, request_id: str) -> List[Dict]:
        """Get all processing steps for a request."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT * FROM processing_steps 
                    WHERE request_id = ? 
                    ORDER BY start_time ASC
                ''', (request_id,))
                
                rows = cursor.fetchall()
                columns = [description[0] for description in cursor.description]
                
                steps = []
                for row in rows:
                    step_data = dict(zip(columns, row))
                    steps.append(step_data)
                
                conn.close()
                return steps
            except Exception as e:
                logger.error(f"Failed to get processing steps for {request_id}: {e}")
                return []
    
    def get_statistics(self) -> Dict:
        """Get monitoring statistics."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Overall statistics
                cursor.execute('SELECT COUNT(*) FROM requests')
                total_requests = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM requests WHERE status = 'completed'")
                completed_requests = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM requests WHERE status = 'failed'")
                failed_requests = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM requests WHERE status = 'processing'")
                processing_requests = cursor.fetchone()[0]
                
                # Success rate
                success_rate = (completed_requests / total_requests * 100) if total_requests > 0 else 0
                
                # Average processing time
                cursor.execute("SELECT AVG(processing_time) FROM requests WHERE status = 'completed'")
                avg_processing_time = cursor.fetchone()[0] or 0
                
                # Recent activity (last 24 hours)
                cursor.execute("""
                    SELECT COUNT(*) FROM requests 
                    WHERE timestamp >= datetime('now', '-1 day')
                """)
                recent_requests = cursor.fetchone()[0]
                
                conn.close()
                
                return {
                    'total_requests': total_requests,
                    'completed_requests': completed_requests,
                    'failed_requests': failed_requests,
                    'processing_requests': processing_requests,
                    'success_rate': round(success_rate, 2),
                    'average_processing_time': round(avg_processing_time, 2),
                    'recent_requests_24h': recent_requests
                }
            except Exception as e:
                logger.error(f"Failed to get statistics: {e}")
                return {}
    
    def cleanup_old_records(self, days_to_keep: int = 30) -> bool:
        """Clean up old records to prevent database bloat."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Delete old requests and their processing steps
                cursor.execute(f"""
                    DELETE FROM processing_steps 
                    WHERE request_id IN (
                        SELECT id FROM requests 
                        WHERE timestamp < datetime('now', '-{days_to_keep} days')
                    )
                """)
                
                cursor.execute(f"""
                    DELETE FROM requests 
                    WHERE timestamp < datetime('now', '-{days_to_keep} days')
                """)
                
                conn.commit()
                conn.close()
                
                logger.info(f"Cleaned up records older than {days_to_keep} days")
                return True
            except Exception as e:
                logger.error(f"Failed to cleanup old records: {e}")
                return False

# Global database instance
monitor_db = MonitorDatabase()