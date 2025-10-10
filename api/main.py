# -----------------------------------------------------------------------------
# Copyright (c) 2025 Edureka Backend
# All rights reserved.
#
# Developed by: GiKA AI Team
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhath@gikagraph.ai
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

"""FastAPI application for Entity-Scoped RAG System"""

import os
import sys
import uuid
import asyncio
import time
import psutil
import threading
import queue
from datetime import datetime
from typing import Dict, List, Optional, Callable
from pathlib import Path
from threading import Lock
from concurrent.futures import Future, as_completed

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .models import (
    EntityCreate, EntityResponse, EntityListResponse,
    FileUploadResponse, FileDeleteResponse, DocumentInfo,
    ChatSessionCreate, ChatSessionResponse, ChatMessage, ChatMessageRole,
    ChatRequest, ChatResponse,
    SearchRequest, SearchResult, SearchResponse,
    HealthResponse, ErrorResponse,
    TaskStatus, FileUploadTask, TaskStatusResponse
)

# Import RAG system components
from src.core.rag_system import (
    index_document_entity_scoped,
    search_entity_scoped,
    get_entity_stats,
    get_all_entity_stats,
    delete_document_entity_scoped
)
from src.core.entity_scoped_rag import get_entity_rag_manager
from src.core.agents.research_agent import ResearchAgent
from src.core.agents.custom_types import ResponseRequiredRequest, Utterance
from src.infrastructure.storage.json_storage import JSONStorage
from src.log_creator import get_file_logger
from src.config import Config

logger = get_file_logger()

# Initialize FastAPI app
app = FastAPI(
    title="Entity-Scoped RAG API",
    description="High-performance RAG system with isolated indexes per entity",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent storage for entities and chat sessions using JSONStorage
STORAGE_DIR = Path(Config.DATA_DIR) / "api_storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

entities_storage = JSONStorage(str(STORAGE_DIR), enable_sharding=False)
chat_sessions_storage = JSONStorage(str(STORAGE_DIR), enable_sharding=False)

ENTITIES_COLLECTION = "entities"
CHAT_SESSIONS_COLLECTION = "chat_sessions"

# In-memory cache for session agents (reconstructed on startup)
session_agents: Dict[str, ResearchAgent] = {}

# Upload directory
UPLOAD_DIR = Path(Config.DATA_DIR) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# Concurrency Control Configuration
# ============================================================================
"""
Concurrency Control Design:
- Uses DynamicThreadPool for background file upload processing with CPU-aware scaling
- Uses asyncio Semaphores for chat and search operations
- Separate limits for different operation types based on industry standards:
  * Chat: 20 concurrent requests (user-facing, needs high throughput and low latency)
  * Upload: 2-8 concurrent background tasks (dynamic, based on CPU utilization)
  * Search: 10 concurrent requests (moderate resource usage, balanced performance)

- Chat and Search operations are I/O-bound and use async/await (non-blocking)
- Upload operations run in background DynamicThreadPool with task tracking
- Clients poll for upload status using task_id
- Upload workers scale dynamically based on CPU utilization and queue size
"""

# Configuration for different operation types
CHAT_MAX_WORKERS = 20      # High throughput for user-facing chat
UPLOAD_MIN_WORKERS = 2     # Minimum upload workers
UPLOAD_MAX_WORKERS = max(2, int(os.cpu_count() * 0.8))  # 80% of vCPUs
SEARCH_MAX_WORKERS = 10    # Moderate for search operations

# Dynamic worker configuration for uploads
CPU_UTILIZATION_THRESHOLD = 80.0  # Only scale up if CPU < 80%
CPU_CHECK_INTERVAL = 10  # Check CPU every 10 seconds
SCALE_UP_COOLDOWN = 15  # Wait 15 seconds after scaling up
SCALE_DOWN_COOLDOWN = 5  # Wait 5 seconds after scaling down

# Scaling state tracking
last_scale_up_time = 0.0
last_scale_down_time = 0.0


class DynamicThreadPool:
    """Custom thread pool that can dynamically scale workers based on CPU utilization"""

    def __init__(self, min_workers: int = 2, max_workers: int = 10):
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.task_queue = queue.Queue()
        self.workers: List[threading.Thread] = []
        self.lock = threading.Lock()
        self.shutdown_flag = threading.Event()
        self.active_tasks = 0
        self.active_tasks_lock = threading.Lock()

        # Start with minimum workers
        self._scale_to(min_workers)

    def _worker(self):
        """Worker thread that processes tasks from the queue"""
        while not self.shutdown_flag.is_set():
            try:
                # Wait for a task with timeout to allow checking shutdown flag
                task_item = self.task_queue.get(timeout=1)
                if task_item is None:  # Poison pill to stop worker
                    self.task_queue.task_done()
                    break

                func, args, kwargs, future = task_item

                with self.active_tasks_lock:
                    self.active_tasks += 1

                try:
                    result = func(*args, **kwargs)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
                finally:
                    with self.active_tasks_lock:
                        self.active_tasks -= 1
                    self.task_queue.task_done()

            except queue.Empty:
                continue

    def _scale_to(self, target_workers: int):
        """Scale the thread pool to the target number of workers"""
        with self.lock:
            current_count = len(self.workers)

            if target_workers > current_count:
                # Scale up - add more workers
                for _ in range(target_workers - current_count):
                    worker = threading.Thread(target=self._worker, daemon=True)
                    worker.start()
                    self.workers.append(worker)

            elif target_workers < current_count:
                # Scale down - remove workers
                workers_to_remove = current_count - target_workers
                for _ in range(workers_to_remove):
                    self.task_queue.put(None)  # Poison pill

                # Remove dead workers from list
                self.workers = [w for w in self.workers if w.is_alive()]

    def submit(self, func: Callable, *args, **kwargs) -> Future:
        """Submit a task to the thread pool"""
        future = Future()
        self.task_queue.put((func, args, kwargs, future))
        return future

    def get_worker_count(self) -> int:
        """Get the current number of active workers"""
        with self.lock:
            # Clean up dead workers
            self.workers = [w for w in self.workers if w.is_alive()]
            return len(self.workers)

    def get_active_tasks(self) -> int:
        """Get the number of currently executing tasks"""
        with self.active_tasks_lock:
            return self.active_tasks

    def get_queue_size(self) -> int:
        """Get the number of queued tasks"""
        return self.task_queue.qsize()

    def scale_workers(self, target: int):
        """Dynamically scale workers to target count"""
        target = max(self.min_workers, min(self.max_workers, target))
        current = self.get_worker_count()

        if target != current:
            self._scale_to(target)
            return True
        return False

    def shutdown(self, wait: bool = True):
        """Shutdown the thread pool"""
        self.shutdown_flag.set()

        # Send poison pills to all workers
        with self.lock:
            for _ in self.workers:
                self.task_queue.put(None)

        if wait:
            for worker in self.workers:
                worker.join(timeout=5)


# DynamicThreadPool for background upload tasks
upload_executor = DynamicThreadPool(min_workers=UPLOAD_MIN_WORKERS, max_workers=UPLOAD_MAX_WORKERS)
upload_executor_lock = threading.Lock()
cpu_monitor_task = None

# Semaphores for chat and search operations
chat_semaphore = asyncio.Semaphore(CHAT_MAX_WORKERS)
search_semaphore = asyncio.Semaphore(SEARCH_MAX_WORKERS)

# Track active processing tasks by type
active_tasks_lock = Lock()
active_chat_count = 0
active_search_count = 0

# Task tracking for background uploads
upload_tasks: Dict[str, FileUploadTask] = {}
upload_tasks_lock = Lock()

def get_cpu_utilization() -> float:
    """Get current CPU utilization percentage"""
    return psutil.cpu_percent(interval=1)


def calculate_optimal_workers(cpu_util: float, queue_size: int = 0) -> int:
    """
    Calculate optimal number of workers based on CPU utilization and queue size

    Args:
        cpu_util: Current CPU utilization percentage
        queue_size: Number of tasks waiting in queue

    Returns:
        Number of workers to use
    """
    current_workers = upload_executor.get_worker_count()

    if cpu_util >= CPU_UTILIZATION_THRESHOLD:
        # CPU is at or above threshold, scale down aggressively
        cpu_overage = cpu_util - CPU_UTILIZATION_THRESHOLD

        if cpu_overage >= 15:  # CPU > 95%
            # Drastic scale down - remove half the workers above minimum
            workers_above_min = current_workers - UPLOAD_MIN_WORKERS
            return max(UPLOAD_MIN_WORKERS, current_workers - max(2, workers_above_min // 2))
        elif cpu_overage >= 10:  # CPU 90-95%
            # Aggressive scale down - remove 2 workers
            return max(UPLOAD_MIN_WORKERS, current_workers - 2)
        else:  # CPU 80-90%
            # Moderate scale down - remove 1 worker
            return max(UPLOAD_MIN_WORKERS, current_workers - 1)

    # CPU is below threshold, we can scale up
    # Scale workers based on how much headroom we have
    headroom = CPU_UTILIZATION_THRESHOLD - cpu_util
    scale_factor = min(1.0, headroom / CPU_UTILIZATION_THRESHOLD)

    # Calculate target workers
    target_workers = int(UPLOAD_MIN_WORKERS + (UPLOAD_MAX_WORKERS - UPLOAD_MIN_WORKERS) * scale_factor)

    # If there are queued tasks and CPU is low, scale up faster
    if queue_size > current_workers and cpu_util < CPU_UTILIZATION_THRESHOLD * 0.5:
        target_workers = min(UPLOAD_MAX_WORKERS, current_workers + 2)

    return max(UPLOAD_MIN_WORKERS, min(UPLOAD_MAX_WORKERS, target_workers))


def adjust_worker_pool():
    """
    Dynamically adjust thread pool size based on current CPU utilization
    Enforces cooldown periods to allow new threads to stabilize before further scaling
    """
    global last_scale_up_time, last_scale_down_time

    try:
        cpu_util = get_cpu_utilization()
        queue_size = upload_executor.get_queue_size()
        active_tasks = upload_executor.get_active_tasks()
        current_workers = upload_executor.get_worker_count()
        current_time = time.time()

        optimal_workers = calculate_optimal_workers(cpu_util, queue_size)

        if optimal_workers != current_workers:
            # Determine if this is a scale up or scale down
            is_scale_up = optimal_workers > current_workers
            is_scale_down = optimal_workers < current_workers

            # Check cooldown periods
            if is_scale_up:
                time_since_last_scale_up = current_time - last_scale_up_time
                if time_since_last_scale_up < SCALE_UP_COOLDOWN:
                    logger.debug(
                        f"[CPU Monitor] Scale-up blocked (cooldown: {SCALE_UP_COOLDOWN - time_since_last_scale_up:.1f}s remaining). "
                        f"Current: {current_workers}, Target: {optimal_workers}, CPU: {cpu_util:.1f}%"
                    )
                    return

            elif is_scale_down:
                time_since_last_scale_down = current_time - last_scale_down_time
                if time_since_last_scale_down < SCALE_DOWN_COOLDOWN:
                    logger.debug(
                        f"[CPU Monitor] Scale-down blocked (cooldown: {SCALE_DOWN_COOLDOWN - time_since_last_scale_down:.1f}s remaining). "
                        f"Current: {current_workers}, Target: {optimal_workers}, CPU: {cpu_util:.1f}%"
                    )
                    return

            # Perform scaling
            with upload_executor_lock:
                success = upload_executor.scale_workers(optimal_workers)
                if success:
                    # Update cooldown timers
                    if is_scale_up:
                        last_scale_up_time = current_time
                    elif is_scale_down:
                        last_scale_down_time = current_time

                    logger.info(
                        f"[CPU Monitor] Scaled upload workers: {current_workers} → {optimal_workers} "
                        f"({'UP' if is_scale_up else 'DOWN'}) - "
                        f"CPU: {cpu_util:.1f}%, Queue: {queue_size}, Active: {active_tasks}"
                    )
        else:
            logger.debug(
                f"[CPU Monitor] Upload workers optimal: {current_workers} "
                f"(CPU: {cpu_util:.1f}%, Queue: {queue_size}, Active: {active_tasks})"
            )

    except Exception as e:
        logger.error(f"[CPU Monitor] Error adjusting workers: {str(e)}")


async def cpu_monitoring_loop():
    """Background task to monitor CPU and adjust workers"""
    logger.info(f"[CPU Monitor] Started monitoring (interval: {CPU_CHECK_INTERVAL}s)")

    while True:
        try:
            await asyncio.sleep(CPU_CHECK_INTERVAL)
            adjust_worker_pool()
        except asyncio.CancelledError:
            logger.info("[CPU Monitor] Monitoring stopped")
            break
        except Exception as e:
            logger.error(f"[CPU Monitor] Error in monitoring loop: {str(e)}")


def get_active_tasks_count(operation_type: str = "all") -> int:
    """Get the current number of active processing tasks"""
    with active_tasks_lock:
        if operation_type == "chat":
            return active_chat_count
        elif operation_type == "upload":
            # Return actual active tasks from executor
            return upload_executor.get_active_tasks()
        elif operation_type == "search":
            return active_search_count
        else:
            upload_count = upload_executor.get_active_tasks()
            return active_chat_count + upload_count + active_search_count


def increment_active_tasks(operation_type: str):
    """Increment the active tasks counter for specific operation type"""
    global active_chat_count, active_search_count
    with active_tasks_lock:
        if operation_type == "chat":
            active_chat_count += 1
        elif operation_type == "search":
            active_search_count += 1


def decrement_active_tasks(operation_type: str):
    """Decrement the active tasks counter for specific operation type"""
    global active_chat_count, active_search_count
    with active_tasks_lock:
        if operation_type == "chat":
            active_chat_count -= 1
        elif operation_type == "search":
            active_search_count -= 1

# Helper functions for persistent storage
def get_entities_db() -> Dict[str, Dict]:
    """Get entities database with persistence"""
    data = entities_storage.find(ENTITIES_COLLECTION)
    return {entity["entity_id"]: entity for entity in data}

def save_entity(entity_data: Dict):
    """Save entity to persistent storage"""
    # Use update_one with upsert=True to insert or update
    entities_storage.update_one(
        ENTITIES_COLLECTION,
        {"entity_id": entity_data["entity_id"]},
        {"$set": entity_data},
        upsert=True
    )

def delete_entity_from_storage(entity_id: str):
    """Delete entity from persistent storage"""
    entities_storage.delete_one(ENTITIES_COLLECTION, {"entity_id": entity_id})

def get_entity_from_storage(entity_id: str) -> Optional[Dict]:
    """Get entity from persistent storage"""
    return entities_storage.find_one(ENTITIES_COLLECTION, {"entity_id": entity_id})

def get_chat_sessions_db() -> Dict[str, Dict]:
    """Get chat sessions database with persistence"""
    data = chat_sessions_storage.find(CHAT_SESSIONS_COLLECTION)
    return {sess["session_id"]: sess for sess in data}

def save_chat_session(session_data: Dict):
    """Save chat session to persistent storage"""
    # Use update_one with upsert=True to insert or update
    chat_sessions_storage.update_one(
        CHAT_SESSIONS_COLLECTION,
        {"session_id": session_data["session_id"]},
        {"$set": session_data},
        upsert=True
    )

def delete_chat_session_from_storage(session_id: str):
    """Delete chat session from persistent storage"""
    chat_sessions_storage.delete_one(CHAT_SESSIONS_COLLECTION, {"session_id": session_id})

def get_chat_session_from_storage(session_id: str) -> Optional[Dict]:
    """Get chat session from persistent storage"""
    return chat_sessions_storage.find_one(CHAT_SESSIONS_COLLECTION, {"session_id": session_id})


# ============================================================================
# Entity Management Endpoints
# ============================================================================

@app.post("/api/entities", response_model=EntityResponse, tags=["Entities"])
async def create_entity(entity: EntityCreate):
    """
    Create a new entity instance

    - **entity_id**: Unique identifier (e.g., "company_123")
    - **entity_name**: Display name (e.g., "TechCorp Industries")
    - **description**: Optional description
    - **metadata**: Additional metadata
    """
    try:
        # Check if entity already exists
        existing_entity = get_entity_from_storage(entity.entity_id)
        if existing_entity:
            raise HTTPException(status_code=400, detail=f"Entity {entity.entity_id} already exists")

        # Initialize entity-scoped store
        manager = get_entity_rag_manager()
        entity_store = manager.get_entity_store(entity.entity_id)

        # Store entity info
        entity_data = {
            "entity_id": entity.entity_id,
            "entity_name": entity.entity_name,
            "description": entity.description,
            "metadata": entity.metadata or {},
            "created_at": datetime.utcnow().isoformat(),
            "documents": []
        }
        save_entity(entity_data)

        # Get stats
        stats = get_entity_stats(entity.entity_id)

        logger.info(f"Created entity: {entity.entity_id} ({entity.entity_name})")

        return EntityResponse(
            entity_id=entity.entity_id,
            entity_name=entity.entity_name,
            description=entity.description,
            metadata=entity.metadata,
            created_at=datetime.fromisoformat(entity_data["created_at"]),
            total_documents=stats.get("total_documents", 0),
            total_chunks=stats.get("total_chunks", 0),
            has_vector_store=stats.get("has_vector_store", False)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/{entity_id}", response_model=EntityResponse, tags=["Entities"])
async def get_entity(entity_id: str):
    """Get entity details by ID"""
    try:
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        stats = get_entity_stats(entity_id)

        return EntityResponse(
            entity_id=entity_data["entity_id"],
            entity_name=entity_data["entity_name"],
            description=entity_data.get("description"),
            metadata=entity_data.get("metadata"),
            created_at=datetime.fromisoformat(entity_data["created_at"]) if isinstance(entity_data["created_at"], str) else entity_data["created_at"],
            total_documents=stats.get("total_documents", 0),
            total_chunks=stats.get("total_chunks", 0),
            has_vector_store=stats.get("has_vector_store", False)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities", response_model=EntityListResponse, tags=["Entities"])
async def list_entities():
    """List all entities"""
    try:
        all_stats = get_all_entity_stats()
        entities = []

        entities_db = get_entities_db()
        for entity_id, entity_data in entities_db.items():
            stats = all_stats.get(entity_id, {})
            entities.append(EntityResponse(
                entity_id=entity_data["entity_id"],
                entity_name=entity_data["entity_name"],
                description=entity_data.get("description"),
                metadata=entity_data.get("metadata"),
                created_at=datetime.fromisoformat(entity_data["created_at"]) if isinstance(entity_data["created_at"], str) else entity_data["created_at"],
                total_documents=stats.get("total_documents", 0),
                total_chunks=stats.get("total_chunks", 0),
                has_vector_store=stats.get("has_vector_store", False)
            ))

        return EntityListResponse(entities=entities, total=len(entities))

    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/entities/{entity_id}", tags=["Entities"])
async def delete_entity(entity_id: str):
    """
    Delete an entity and all its data

    **Warning:** This will delete all documents and chat sessions for this entity!
    """
    try:
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Delete all chat sessions for this entity
        chat_sessions_db = get_chat_sessions_db()
        sessions_to_delete = [
            sid for sid, session in chat_sessions_db.items()
            if session["entity_id"] == entity_id
        ]
        for session_id in sessions_to_delete:
            delete_chat_session_from_storage(session_id)
            if session_id in session_agents:
                del session_agents[session_id]

        # Clean up entity store
        manager = get_entity_rag_manager()
        manager.cleanup_entity(entity_id)

        # Remove from database
        delete_entity_from_storage(entity_id)

        logger.info(f"Deleted entity: {entity_id}")

        return {
            "success": True,
            "entity_id": entity_id,
            "message": f"Entity {entity_id} and all associated data deleted",
            "sessions_deleted": len(sessions_to_delete)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# File Management Endpoints
# ============================================================================

def _process_file_upload(
    task_id: str,
    entity_id: str,
    file_path: Path,
    filename: str,
    description: Optional[str],
    source: Optional[str]
):
    """
    Background worker function to process file upload
    Runs in ThreadPoolExecutor
    """
    try:
        # Update task status to processing
        with upload_tasks_lock:
            upload_tasks[task_id].status = TaskStatus.PROCESSING
            upload_tasks[task_id].updated_at = datetime.utcnow()
            upload_tasks[task_id].message = "Processing file upload"

        logger.info(f"Processing file upload for task {task_id} (active: {get_active_tasks_count('upload')}/{UPLOAD_MAX_WORKERS})")

        # Index the file
        metadata = {
            "description": description,
            "uploaded_at": datetime.utcnow().isoformat(),
            "original_filename": filename
        }

        if source:
            metadata["source"] = source

        result = index_document_entity_scoped(
            entity_id=entity_id,
            file_path=str(file_path),
            metadata=metadata
        )

        if not result:
            raise Exception("Failed to index document")

        # Update entity documents list
        entity_data = get_entity_from_storage(entity_id)
        if entity_data:
            entity_data["documents"].append({
                "doc_id": result["doc_id"],
                "filename": filename,
                "file_path": str(file_path),
                "uploaded_at": datetime.utcnow().isoformat(),
                "source": source or str(file_path)
            })
            save_entity(entity_data)

        logger.info(f"Uploaded file {filename} to entity {entity_id}, doc_id: {result['doc_id']}, source: {source or 'default'}")

        # Update task with success result
        upload_result = FileUploadResponse(
            success=True,
            doc_id=result["doc_id"],
            entity_id=entity_id,
            filename=filename,
            chunks_count=result.get("chunks_count", 0),
            is_duplicate=result.get("is_duplicate", False),
            message=f"File uploaded and indexed successfully"
        )

        with upload_tasks_lock:
            upload_tasks[task_id].status = TaskStatus.COMPLETED
            upload_tasks[task_id].updated_at = datetime.utcnow()
            upload_tasks[task_id].message = "File uploaded and indexed successfully"
            upload_tasks[task_id].result = upload_result

        logger.info(f"Completed file upload for task {task_id}")

    except Exception as e:
        logger.error(f"Error processing file upload for task {task_id}: {e}")
        with upload_tasks_lock:
            upload_tasks[task_id].status = TaskStatus.FAILED
            upload_tasks[task_id].updated_at = datetime.utcnow()
            upload_tasks[task_id].error = str(e)
            upload_tasks[task_id].message = f"Failed to process file: {str(e)}"


@app.post("/api/entities/{entity_id}/files", response_model=TaskStatusResponse, tags=["Files"])
async def upload_file(
    entity_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    source: Optional[str] = Form(None)
):
    """
    Upload a file to an entity (async - returns task_id)

    Args:
        entity_id: Entity identifier
        file: File to upload
        description: Optional description
        source: Optional source identifier for chunking (e.g., 'company_123/financials/report.pdf')

    Returns task_id to poll for status
    File processing happens in background ThreadPoolExecutor
    """
    try:
        # Check if entity exists
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Generate task ID
        task_id = f"upload_{uuid.uuid4().hex[:12]}"

        # Save uploaded file
        file_path = UPLOAD_DIR / entity_id / file.filename
        file_path.parent.mkdir(parents=True, exist_ok=True)

        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Create task record
        task = FileUploadTask(
            task_id=task_id,
            entity_id=entity_id,
            filename=file.filename,
            status=TaskStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            message="File uploaded, queued for processing"
        )

        with upload_tasks_lock:
            upload_tasks[task_id] = task

        # Submit to background executor
        upload_executor.submit(
            _process_file_upload,
            task_id,
            entity_id,
            file_path,
            file.filename,
            description,
            source
        )

        logger.info(f"Created upload task {task_id} for file {file.filename} in entity {entity_id}")

        return TaskStatusResponse(
            task_id=task_id,
            status=TaskStatus.PENDING,
            created_at=task.created_at,
            updated_at=task.updated_at,
            message=task.message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating upload task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}", response_model=TaskStatusResponse, tags=["Files"])
async def get_task_status(task_id: str):
    """
    Get the status of an upload task

    Poll this endpoint to check upload progress
    """
    try:
        with upload_tasks_lock:
            task = upload_tasks.get(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Build response
        response = TaskStatusResponse(
            task_id=task.task_id,
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
            message=task.message,
            error=task.error
        )

        # Include result if completed
        if task.status == TaskStatus.COMPLETED and task.result:
            response.result = task.result.model_dump()

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/{entity_id}/files", response_model=List[DocumentInfo], tags=["Files"])
async def list_files(entity_id: str):
    """List all files for an entity"""
    try:
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        documents = entity_data.get("documents", [])

        return [
            DocumentInfo(
                doc_id=doc["doc_id"],
                doc_name=doc["filename"],
                file_path=doc.get("file_path"),
                indexed_at=datetime.fromisoformat(doc["uploaded_at"]) if isinstance(doc.get("uploaded_at"), str) else doc.get("uploaded_at")
            )
            for doc in documents
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/entities/{entity_id}/files/{doc_id}", response_model=FileDeleteResponse, tags=["Files"])
async def delete_file(entity_id: str, doc_id: str):
    """Delete a file from an entity"""
    try:
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Find document in entity
        documents = entity_data["documents"]
        doc_to_delete = None
        for i, doc in enumerate(documents):
            if doc["doc_id"] == doc_id:
                doc_to_delete = documents.pop(i)
                break

        if not doc_to_delete:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found in entity {entity_id}")

        # Save updated entity data
        save_entity(entity_data)

        # Delete from entity-scoped RAG
        success = delete_document_entity_scoped(entity_id, doc_id)

        if not success:
            logger.warning(f"Failed to delete document {doc_id} from entity store")

        # Delete physical file if exists
        file_path = Path(doc_to_delete.get("file_path", ""))
        if file_path.exists():
            file_path.unlink()

        logger.info(f"Deleted document {doc_id} from entity {entity_id}")

        return FileDeleteResponse(
            success=True,
            doc_id=doc_id,
            entity_id=entity_id,
            message=f"Document {doc_id} deleted successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Chat Session Endpoints
# ============================================================================

@app.post("/api/chat/sessions", response_model=ChatSessionResponse, tags=["Chat"])
async def create_chat_session(session: ChatSessionCreate):
    """
    Create a new chat session for an entity

    Multiple sessions can be created for the same entity
    """
    try:
        # Check if entity exists
        entity_data = get_entity_from_storage(session.entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {session.entity_id} not found")

        # Generate session ID
        session_id = f"session_{uuid.uuid4().hex[:12]}"

        # Get current session count for naming
        chat_sessions_db = get_chat_sessions_db()
        session_count = len([s for s in chat_sessions_db.values() if s["entity_id"] == session.entity_id])

        # Create session data
        session_data = {
            "session_id": session_id,
            "entity_id": session.entity_id,
            "entity_name": entity_data["entity_name"],
            "session_name": session.session_name or f"Chat {session_count + 1}",
            "created_at": datetime.utcnow().isoformat(),
            "last_activity": datetime.utcnow().isoformat(),
            "messages": [],
            "metadata": session.metadata or {}
        }

        save_chat_session(session_data)

        # Initialize research agent for this session
        agent = ResearchAgent(
            id=session.entity_id,
            entity_name=entity_data["entity_name"],
            use_entity_scoped=True
        )
        session_agents[session_id] = agent

        logger.info(f"Created chat session {session_id} for entity {session.entity_id}")

        return ChatSessionResponse(
            session_id=session_id,
            entity_id=session.entity_id,
            entity_name=entity_data["entity_name"],
            session_name=session_data["session_name"],
            created_at=datetime.fromisoformat(session_data["created_at"]),
            last_activity=datetime.fromisoformat(session_data["last_activity"]),
            message_count=0,
            metadata=session.metadata
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating chat session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chat/sessions/{session_id}", response_model=ChatSessionResponse, tags=["Chat"])
async def get_chat_session(session_id: str):
    """Get chat session details"""
    try:
        session_data = get_chat_session_from_storage(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        # Reconstruct agent if not in memory
        if session_id not in session_agents:
            entity_data = get_entity_from_storage(session_data["entity_id"])
            if entity_data:
                agent = ResearchAgent(
                    id=session_data["entity_id"],
                    entity_name=entity_data["entity_name"],
                    use_entity_scoped=True
                )
                session_agents[session_id] = agent

        return ChatSessionResponse(
            session_id=session_data["session_id"],
            entity_id=session_data["entity_id"],
            entity_name=session_data["entity_name"],
            session_name=session_data.get("session_name"),
            created_at=datetime.fromisoformat(session_data["created_at"]) if isinstance(session_data["created_at"], str) else session_data["created_at"],
            last_activity=datetime.fromisoformat(session_data["last_activity"]) if isinstance(session_data["last_activity"], str) else session_data["last_activity"],
            message_count=len(session_data["messages"]),
            metadata=session_data.get("metadata")
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/{entity_id}/sessions", response_model=List[ChatSessionResponse], tags=["Chat"])
async def list_entity_sessions(entity_id: str):
    """List all chat sessions for an entity"""
    try:
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        sessions = []
        chat_sessions_db = get_chat_sessions_db()
        for session_id, session_data in chat_sessions_db.items():
            if session_data["entity_id"] == entity_id:
                sessions.append(ChatSessionResponse(
                    session_id=session_data["session_id"],
                    entity_id=session_data["entity_id"],
                    entity_name=session_data["entity_name"],
                    session_name=session_data.get("session_name"),
                    created_at=datetime.fromisoformat(session_data["created_at"]) if isinstance(session_data["created_at"], str) else session_data["created_at"],
                    last_activity=datetime.fromisoformat(session_data["last_activity"]) if isinstance(session_data["last_activity"], str) else session_data["last_activity"],
                    message_count=len(session_data["messages"]),
                    metadata=session_data.get("metadata")
                ))

        return sessions

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/chat/sessions/{session_id}", tags=["Chat"])
async def delete_chat_session(session_id: str):
    """Delete a chat session"""
    try:
        session_data = get_chat_session_from_storage(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        delete_chat_session_from_storage(session_id)

        if session_id in session_agents:
            del session_agents[session_id]

        logger.info(f"Deleted chat session {session_id}")

        return {
            "success": True,
            "session_id": session_id,
            "message": f"Session deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chat/sessions/{session_id}/messages", response_model=List[ChatMessage], tags=["Chat"])
async def get_chat_history(session_id: str):
    """Get chat history for a session"""
    try:
        session_data = get_chat_session_from_storage(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        # Convert message timestamps
        messages = []
        for msg_dict in session_data["messages"]:
            msg = ChatMessage(
                role=msg_dict["role"],
                content=msg_dict["content"],
                timestamp=datetime.fromisoformat(msg_dict["timestamp"]) if isinstance(msg_dict["timestamp"], str) else msg_dict["timestamp"]
            )
            messages.append(msg)
        return messages

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat", tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Send a message in a chat session

    Supports streaming responses
    Uses semaphore-based concurrency control (max 20 concurrent chats)
    """
    # Acquire semaphore to limit concurrent chat processing
    async with chat_semaphore:
        increment_active_tasks("chat")
        try:
            logger.info(f"Processing chat request (active: {get_active_tasks_count('chat')}/{CHAT_MAX_WORKERS}, total: {get_active_tasks_count()})")

            session_data = get_chat_session_from_storage(request.session_id)
            if not session_data:
                raise HTTPException(status_code=404, detail=f"Session {request.session_id} not found")

            # Reconstruct agent if not in memory
            if request.session_id not in session_agents:
                entity_data = get_entity_from_storage(session_data["entity_id"])
                if not entity_data:
                    raise HTTPException(status_code=500, detail="Entity not found for session")

                agent = ResearchAgent(
                    id=session_data["entity_id"],
                    entity_name=entity_data["entity_name"],
                    use_entity_scoped=True
                )
                session_agents[request.session_id] = agent

            agent = session_agents[request.session_id]

            # Add user message to history
            user_message_dict = {
                "role": ChatMessageRole.USER,
                "content": request.message,
                "timestamp": datetime.utcnow().isoformat()
            }
            session_data["messages"].append(user_message_dict)
            session_data["last_activity"] = datetime.utcnow().isoformat()

            # Save immediately to persist user message
            save_chat_session(session_data)

            # Convert to transcript format (convert role enum to string)
            transcript = [
                {"role": msg["role"].value if hasattr(msg["role"], 'value') else msg["role"], "content": msg["content"]}
                for msg in session_data["messages"]
            ]

            # Stream response
            if request.stream:
                async def generate():
                    full_response = ""
                    try:
                        async for response in agent.research_question(
                            ResponseRequiredRequest(
                                interaction_type="response_required",
                                response_id=len(session_data["messages"]),
                                transcript=transcript
                            ),
                            None
                        ):
                            if response.content:
                                full_response += response.content
                                yield response.content

                            if response.end_call or response.content_complete:
                                # Save assistant message
                                assistant_message_dict = {
                                    "role": ChatMessageRole.ASSISTANT,
                                    "content": full_response,
                                    "timestamp": datetime.utcnow().isoformat()
                                }
                                session_data["messages"].append(assistant_message_dict)
                                session_data["last_activity"] = datetime.utcnow().isoformat()
                                save_chat_session(session_data)
                                break
                    finally:
                        decrement_active_tasks("chat")
                        logger.info(f"Completed chat request (active: {get_active_tasks_count('chat')}/{CHAT_MAX_WORKERS}, total: {get_active_tasks_count()})")

                return StreamingResponse(generate(), media_type="text/plain")
            else:
                # Non-streaming response
                full_response = ""
                async for response in agent.research_question(
                    ResponseRequiredRequest(
                        interaction_type="response_required",
                        response_id=len(session_data["messages"]),
                        transcript=transcript
                    ),
                    None
                ):
                    if response.content:
                        full_response += response.content

                    if response.end_call or response.content_complete:
                        break

                # Save assistant message
                assistant_message_dict = {
                    "role": ChatMessageRole.ASSISTANT,
                    "content": full_response,
                    "timestamp": datetime.utcnow().isoformat()
                }
                session_data["messages"].append(assistant_message_dict)
                session_data["last_activity"] = datetime.utcnow().isoformat()
                save_chat_session(session_data)

                # Convert back to ChatMessage for response
                assistant_message = ChatMessage(
                    role=ChatMessageRole.ASSISTANT,
                    content=full_response,
                    timestamp=datetime.utcnow()
                )

                return ChatResponse(
                    session_id=request.session_id,
                    message=assistant_message
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in chat: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            # For non-streaming, decrement here
            if not request.stream:
                decrement_active_tasks("chat")
                logger.info(f"Completed chat request (active: {get_active_tasks_count('chat')}/{CHAT_MAX_WORKERS}, total: {get_active_tasks_count()})")


# ============================================================================
# Search Endpoints
# ============================================================================

@app.post("/api/search", response_model=SearchResponse, tags=["Search"])
async def search(request: SearchRequest):
    """
    Search within an entity's documents

    Fast entity-scoped search (10-100x faster than global search)
    Uses semaphore-based concurrency control (max 10 concurrent searches)
    """
    # Acquire semaphore to limit concurrent search processing
    async with search_semaphore:
        increment_active_tasks("search")
        try:
            logger.info(f"Processing search request (active: {get_active_tasks_count('search')}/{SEARCH_MAX_WORKERS}, total: {get_active_tasks_count()})")

            entity_data = get_entity_from_storage(request.entity_id)
            if not entity_data:
                raise HTTPException(status_code=404, detail=f"Entity {request.entity_id} not found")

            # Perform entity-scoped search
            results_docs = search_entity_scoped(
                entity_id=request.entity_id,
                query=request.query,
                k=request.k,
                doc_ids=request.doc_ids
            )

            # Convert to response format
            results = []
            for doc in results_docs:
                metadata = doc.metadata
                results.append(SearchResult(
                    content=doc.page_content,
                    doc_id=metadata.get('metadata', {}).get('doc_id', 'unknown'),
                    chunk_order_index=metadata.get('chunk', {}).get('chunk_order_index', 0),
                    source=metadata.get('chunk', {}).get('source')
                ))

            return SearchResponse(
                entity_id=request.entity_id,
                query=request.query,
                results=results,
                total=len(results)
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in search: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            decrement_active_tasks("search")
            logger.info(f"Completed search request (active: {get_active_tasks_count('search')}/{SEARCH_MAX_WORKERS}, total: {get_active_tasks_count()})")


# ============================================================================
# Health & Info Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint"""
    try:
        all_stats = get_all_entity_stats()
        total_docs = sum(stats.get("total_documents", 0) for stats in all_stats.values())
        entities_db = get_entities_db()

        return HealthResponse(
            status="healthy",
            version="1.0.0",
            entities_loaded=len(entities_db),
            total_documents=total_docs
        )
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return HealthResponse(
            status="degraded",
            version="1.0.0",
            entities_loaded=0,
            total_documents=0
        )


@app.get("/api/status/workers", tags=["System"])
async def worker_status():
    """Get current concurrency status with breakdown by operation type"""
    chat_active = get_active_tasks_count("chat")
    upload_active = get_active_tasks_count("upload")
    search_active = get_active_tasks_count("search")

    # Get upload task statistics
    with upload_tasks_lock:
        upload_pending = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.PENDING)
        upload_processing = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.PROCESSING)
        upload_completed = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.COMPLETED)
        upload_failed = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.FAILED)

    # Get upload executor stats
    current_upload_workers = upload_executor.get_worker_count()
    upload_queue_size = upload_executor.get_queue_size()
    cpu_util = psutil.cpu_percent(interval=0.1)

    return {
        "total_active": get_active_tasks_count(),
        "execution_model": "hybrid_async_dynamic_threadpool",
        "cpu_utilization": f"{cpu_util:.1f}%",
        "operations": {
            "chat": {
                "active": chat_active,
                "max": CHAT_MAX_WORKERS,
                "available": CHAT_MAX_WORKERS - chat_active,
                "utilization_pct": round((chat_active / CHAT_MAX_WORKERS) * 100, 2),
                "execution": "async_semaphore"
            },
            "upload": {
                "active": upload_active,
                "workers": {
                    "current": current_upload_workers,
                    "min": UPLOAD_MIN_WORKERS,
                    "max": UPLOAD_MAX_WORKERS,
                },
                "queue_size": upload_queue_size,
                "utilization_pct": round((upload_active / current_upload_workers) * 100, 2) if current_upload_workers > 0 else 0,
                "execution": "dynamic_threadpool",
                "scaling": {
                    "cpu_threshold": CPU_UTILIZATION_THRESHOLD,
                    "scale_up_cooldown": SCALE_UP_COOLDOWN,
                    "scale_down_cooldown": SCALE_DOWN_COOLDOWN
                },
                "tasks": {
                    "pending": upload_pending,
                    "processing": upload_processing,
                    "completed": upload_completed,
                    "failed": upload_failed,
                    "total": len(upload_tasks)
                }
            },
            "search": {
                "active": search_active,
                "max": SEARCH_MAX_WORKERS,
                "available": SEARCH_MAX_WORKERS - search_active,
                "utilization_pct": round((search_active / SEARCH_MAX_WORKERS) * 100, 2),
                "execution": "async_semaphore"
            }
        }
    }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint"""
    return {
        "message": "Entity-Scoped RAG API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


# ============================================================================
# Application Lifecycle Events
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    global cpu_monitor_task

    logger.info("Starting Entity-Scoped RAG API")
    logger.info("Using hybrid async/dynamic threadpool execution model")
    logger.info(f"Concurrency limits - Chat: {CHAT_MAX_WORKERS}, Upload: {UPLOAD_MIN_WORKERS}-{UPLOAD_MAX_WORKERS} (Dynamic), Search: {SEARCH_MAX_WORKERS}")
    logger.info(f"CPU monitoring enabled - Threshold: {CPU_UTILIZATION_THRESHOLD}%, Interval: {CPU_CHECK_INTERVAL}s")

    # Start CPU monitoring task
    cpu_monitor_task = asyncio.create_task(cpu_monitoring_loop())
    logger.info("[Startup] CPU monitoring task started")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    global cpu_monitor_task

    logger.info("Shutting down API server...")
    logger.info(f"Active requests at shutdown: {get_active_tasks_count()}")

    # Cancel CPU monitoring task
    if cpu_monitor_task and not cpu_monitor_task.done():
        cpu_monitor_task.cancel()
        try:
            await cpu_monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("[Shutdown] CPU monitoring task stopped")

    # Shutdown DynamicThreadPool
    logger.info("Shutting down upload DynamicThreadPool...")
    upload_executor.shutdown(wait=True)
    logger.info(f"Upload tasks at shutdown - Total: {len(upload_tasks)}, Active: {get_active_tasks_count('upload')}")

    logger.info("Shutdown complete")


# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": str(exc.detail),
            "detail": str(exc)
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc)
        }
    )


# ============================================================================
# Run Server
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
