# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
# All rights reserved.
#
# Developed by: 
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhathchellingi2003@gmail.com
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

# src/infrastructure/database/mongodb.py
from pymongo import MongoClient
from contextlib import contextmanager
import threading
from typing import Optional, Any
import time
import atexit

from ...config import Config
from ...log_creator import get_file_logger

logger = get_file_logger()

class MongoConnectionPool:
    """
    Singleton MongoDB connection pool with automatic cleanup
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self._client: Optional[MongoClient[Any]] = None
        self._last_used = time.time()
        self._cleanup_interval = 300
        self._max_idle_time = 600
        self._lock = threading.RLock()
        self._cleanup_task = None
        self._shutdown = False

        atexit.register(self.close_all_connections)
        
        # Startup cleanup task
        self._start_cleanup_task()
        
    def _start_cleanup_task(self):
        """
        Start background cleanup task
        """
        def cleanup_worker():
            while not self._shutdown:
                try:
                    time.sleep(self._cleanup_interval)
                    if not self._shutdown:
                        self._cleanup_idle_connections()
                except Exception as e:
                    logger.error(f"Error in cleanup worker: {e}")
                
        self._cleanup_task = threading.Thread(target=cleanup_worker, daemon=True)
        self._cleanup_task.start()
        logger.info("Mongo connection pool cleanup task started")
        
    def _cleanup_idle_connections(self):
        """Close connections that have been idle for too long"""
        with self._lock:
            if self._client and (time.time() - self._last_used) > self._max_idle_time:
                try:
                    self._client.close()
                    self._client = None
                    logger.info("Closed idle MongoDB connection")
                except Exception as e:
                    logger.error(f"Error closing idle  MongoDB connection: {e}")
    
    def  get_client(self) -> MongoClient[Any]:
        """Get MongoDB client, creating new connection if needed"""
        with self._lock:
            if self._client is None:
                logger.info("Creating new MongoDB connection")
                try:
                    self._client = MongoClient(
                        Config.MONGODB_URL,
                        maxPoolSize=20,  # Maximum connections in pool
                        minPoolSize=5,   # Minimum connections to maintain
                        maxIdleTimeMS=300000,  # 5 minutes
                        connectTimeoutMS=5000,  # 5 seconds (correct parameter name)
                        serverSelectionTimeoutMS=5000,  # 5 seconds
                        socketTimeoutMS=30000,  # 30 seconds for large queries
                        retryWrites=True,
                        retryReads=True,
                        maxConnecting=2,
                    )
                    # Test Connection
                    self._client.admin.command('ping')
                    logger.info("MongoDB connection established")
                except Exception as e:
                    logger.error(f"Failed to create MongoDB connection: {e}")
                    self._client = None
                    raise
                
            self._last_used = time.time()
            return self._client

    def get_database(self):
        """Get database instance"""
        client = self.get_client()
        return client[Config.DATABASE_NAME]
    
    @contextmanager
    def get_db_context(self):
        """Context manager for database operations with error handling"""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                db = self.get_database()
                yield db
                break  # Success
            except Exception as e:
                retry_count += 1
                logger.error(f"Database operation error (attempt {retry_count}/{max_retries}): {e}")
                
                # Reset connection on error
                with self._lock:
                    if self._client:
                        try:
                            self._client.close()
                        except:
                            pass
                        self._client = None
                
                if retry_count >= max_retries:
                    raise
                time.sleep(1)
        
    def close_all_connections(self):
        """Close all connections - called during shutdown"""
        self._shutdown = True
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                    self._client = None
                    logger.info("All MongoDB connections closed")
                except Exception as e:
                    logger.error(f"Error closing MongoDB connections: {e}")
                    
mongo_pool = MongoConnectionPool()

# Helper functions for common operations
@contextmanager
def get_db_session():
    """Context manager for database operations"""
    with mongo_pool.get_db_context() as db:
        yield db
    
def get_collection(collection_name: str):
    """Get collection from database"""
    db = mongo_pool.get_database()
    return db[collection_name]