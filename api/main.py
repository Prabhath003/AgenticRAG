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

"""FastAPI application for Entity-Scoped RAG System"""

import os
import asyncio
from typing import Dict, Optional, Any, AsyncGenerator
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.responses import StreamingResponse, JSONResponse, ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .models import (
    EntityCreate, ChatSessionCreate, ChatRequest, HealthResponse,
    KnowledgeGraphRequest, ChunkCreate, ChunkIngestResponse,
    ChunkBatchIngestRequest, ChunkBatchIngestResponse
)

from src.log_creator import get_file_logger
from src.core.manager import Manager
from src.core.models import File as ManagerFile
from src.infrastructure.dynamic_thread_pool import cpu_monitoring_loop, executor

logger = get_file_logger()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize resources on startup"""
    global cpu_monitor_task

    logger.info("Starting Entity-Scoped RAG API")
    logger.info("Using hybrid async/dynamic threadpool execution model")

    # Start CPU monitoring task
    cpu_monitor_task = asyncio.create_task(cpu_monitoring_loop())
    logger.info("[Startup] CPU monitoring task started")
    
    try:
        yield
        
        logger.info("Shutting down API server...")
        # Cancel CPU monitoring task
        if cpu_monitor_task and not cpu_monitor_task.done():
            cpu_monitor_task.cancel()
            try:
                await cpu_monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("[Shutdown] CPU monitoring task stopped")

        # Shutdown session cleanup thread
        task_manager.shutdown_session_cleanup()

        # Shutdown DynamicThreadPool
        logger.info("Shutting down upload DynamicThreadPool...")
        executor.shutdown(wait=True)        
    finally:
        logger.info("Shutdown complete")

# Initialize FastAPI app
app = FastAPI(
    title="Entity-Scoped RAG API",
    description="High-performance RAG system with isolated indexes per entity",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    default_response_class=ORJSONResponse
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
task_manager = Manager()

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

cpu_monitor_task = None

# ============================================================================
# Entity Management Endpoints
# ============================================================================

@app.post("/api/entities", tags=["Entities"])
async def create_entity(entity: EntityCreate):
    """
    Create a new entity instance or reactivate a deleted one

    - **entity_id**: Unique identifier (e.g., "company_123")
    - **entity_name**: Display name (e.g., "TechCorp Industries")
    - **description**: Optional description
    - **metadata**: Additional metadata

    Note: If entity_id was previously deleted, it will be reactivated instead of creating a new one.
    """
    try:
        return task_manager.create_entity(
            entity.entity_id,
            entity.entity_name,
            entity.description,
            entity.metadata
        )

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities/{entity_id}", tags=["Entities"])
async def get_entity(entity_id: str):
    """Get entity details by ID"""
    try:
       return task_manager.get_entity(entity_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities", tags=["Entities"])
async def list_entities():
    """List all entities"""
    try:
        return task_manager.list_entities()

    except Exception as e:
        logger.error(f"Error listing entities: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/entities/{entity_id}", tags=["Entities"])
async def delete_entity(entity_id: str) -> Dict[str, Any]:
    """
    Mark an entity as inactive (soft delete)

    The entity and all its data are preserved for audit and tracking purposes.
    Data remains accessible via cost reports and request history.
    """
    try:
        return task_manager.delete_entity(entity_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# File Management Endpoints
# ============================================================================


@app.post("/api/entities/{entity_id}/files", tags=["Files"])
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
        if file.filename:
            # Sanitize filename to prevent path traversal attacks
            filename = os.path.basename(file.filename)
            # Additional validation: remove any remaining path separators
            filename = filename.replace("/", "_").replace("\\", "_")
            if not filename or filename.startswith("."):
                raise HTTPException(status_code=400, detail="Invalid filename")

            content = await file.read()

            return task_manager.upload_file(
                entity_id,
                ManagerFile(filename=filename, content=content),
                description,
                source
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating upload task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tasks/{task_id}", tags=["Files"])
async def get_task_status(task_id: str):
    """
    Get the status of an upload task

    Poll this endpoint to check upload progress
    """
    try:
       return task_manager.get_task_status(task_id)

    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/entities/{entity_id}/files", tags=["Files"])
async def list_files(entity_id: str):
    """List all files for an entity"""
    try:
        return task_manager.list_files(entity_id)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/entities/{entity_id}/chunks", response_model=ChunkIngestResponse, tags=["Files"])
async def ingest_chunk(entity_id: str, chunk: ChunkCreate):
    """
    Ingest a single chunk with automatic duplicate detection

    Each chunk requires a unique chunk_id managed by the client. If a chunk with the same
    chunk_id is submitted twice, it will be detected as a duplicate and not re-indexed.

    Request body should contain:
    - chunk_id: Unique identifier for the chunk (managed by client)
    - markdown: Markdown content with text, chunk_order_index, source, filename, and pages
    - metadata: Chunk metadata including chunk_index, tokens, doc_id, entity_id, and processed_by

    Returns:
    - success: Whether the operation succeeded
    - indexed: Whether the chunk was indexed (False if duplicate)
    - message: Operation description
    """
    try:
        result = task_manager.ingest_chunk(entity_id, chunk.model_dump())
        return ChunkIngestResponse(**result)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ingesting chunk: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/entities/{entity_id}/chunks/batch", response_model=ChunkBatchIngestResponse, tags=["Files"])
async def ingest_chunks_batch(entity_id: str, request: ChunkBatchIngestRequest):
    """
    Batch ingest multiple chunks with automatic duplicate detection

    All chunks must belong to the same document (same doc_id).
    Chunks with duplicate chunk_ids will be detected and skipped.

    Request body should contain:
    - chunks: List of chunk objects, each with:
      - chunk_id: Unique identifier for the chunk
      - markdown: Markdown content
      - metadata: Chunk metadata

    Returns:
    - success: Whether the operation succeeded
    - total_chunks: Total number of chunks submitted
    - indexed_chunks: Number of chunks actually indexed
    - duplicate_chunks: Number of duplicate chunks skipped
    - message: Operation description with counts
    """
    try:
        chunks_data = [chunk.model_dump() for chunk in request.chunks]
        result = task_manager.ingest_chunks(entity_id, chunks_data)
        return ChunkBatchIngestResponse(**result)

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ingesting chunk batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# @app.delete("/api/entities/{entity_id}/files/{doc_id}", tags=["Files"])
# async def delete_file(entity_id: str, doc_id: str):
#     """Mark a file as inactive (soft delete) in an entity"""
#     try:
#         return task_manager.delete_file(entity_id, doc_id)
#     except ValueError as ve:
#         raise HTTPException(status_code=400, detail=str(ve))
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Error deleting file: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Chat Session Endpoints
# ============================================================================

@app.post("/api/chat/sessions", tags=["Chat"])
async def create_chat_session(session: ChatSessionCreate):
    """
    Create a new chat session for an entity

    Multiple sessions can be created for the same entity
    """
    try:
        return task_manager.create_chat_session(
            session.entity_id,
            session.session_name,
            metadata=session.metadata
        )

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating chat session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chat/sessions/{session_id}", tags=["Chat"])
async def get_chat_session(session_id: str):
    """Get chat session details"""
    try:
        return task_manager.get_chat_session(session_id)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/entities/{entity_id}/sessions", tags=["Chat"])
async def list_entity_sessions(entity_id: str):
    """List all chat sessions for an entity"""
    try:
        return task_manager.list_entity_chat_sessions(entity_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/chat/sessions/{session_id}", tags=["Chat"])
async def delete_chat_session(session_id: str) -> Dict[str, Any]:
    """Mark a chat session as inactive (soft delete)"""
    try:
        return task_manager.delete_chat_session(session_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chat/sessions/{session_id}/messages", tags=["Chat"])
async def get_chat_history(session_id: str):
    """Get chat history for a session"""
    try:
        return task_manager.get_chat_session_conversations(session_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat", tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Send a message in a chat session

    Supports streaming responses with metadata
    Uses semaphore-based concurrency control (max 20 concurrent chats)

    Streaming mode (stream=True):
    - Returns newline-delimited JSON (NDJSON) with each object containing:
      - content: Text chunk
      - content_complete: Boolean indicating if response is complete
      - end_call: Boolean indicating if call should end
      - node_ids: List of all context node IDs
      - relationship_ids: List of all navigation relationships
      - cited_node_ids: List of nodes actually cited
      - citations: List of citation details

    Non-streaming mode (stream=False):
    - Returns single JSON response with complete message and all metadata
    """
    # Acquire semaphore to limit concurrent chat processing
    try:
        logger.info(f"Processing chat request")

        # Stream response
        if request.stream:
            async def generate():
                try:
                    response_generator = task_manager.chat_session_converse(
                        request.session_id,
                        request.message,
                        stream=True
                    )
                    async for response_obj in response_generator:
                        # Yield JSON object with newline delimiter
                        yield f"data: {json.dumps(response_obj)}\n\n"
                except ValueError as e:
                    logger.error(f"Error in streaming chat: {e}")
                    raise HTTPException(status_code=404, detail=str(e))
                except Exception as e:
                    logger.error(f"Error in streaming chat: {e}")
                    raise HTTPException(status_code=500, detail=str(e))

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
            )
        else:
            # Non-streaming response
            try:
                response_coro = task_manager.chat_session_converse(
                    request.session_id,
                    request.message,
                    stream=False
                )
                # Await the coroutine returned by the non-streaming chat
                response_data = await response_coro
                return response_data
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                logger.error(f"Error in chat: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Ensure cleanup for both streaming and non-streaming
        pass

# ============================================================================
# Search Endpoints
# ============================================================================

# @app.post("/api/search", response_model=SearchResponse, tags=["Search"])
# async def search(request: SearchRequest):
#     """
#     Search within an entity's documents

#     Fast entity-scoped search (10-100x faster than global search)
#     Uses semaphore-based concurrency control (max 10 concurrent searches)
#     """
#     # Acquire semaphore to limit concurrent search processing
#     async with search_semaphore:
#         increment_active_tasks("search")
#         try:
#             logger.info(f"Processing search request (active: {get_active_tasks_count('search')}/{SEARCH_MAX_WORKERS}, total: {get_active_tasks_count()})")

#             entity_data = get_entity_from_storage(request.entity_id)
#             if not entity_data:
#                 raise HTTPException(status_code=404, detail=f"Entity {request.entity_id} not found")

#             # Perform entity-scoped search
#             results_docs = search_entity_scoped(
#                 entity_id=request.entity_id,
#                 query=request.query,
#                 k=request.k,
#                 doc_ids=request.doc_ids
#             )

#             # Convert to response format
#             results = []
#             for doc in results_docs:
#                 metadata = doc.metadata
#                 results.append(SearchResult(
#                     content=doc.page_content,
#                     doc_id=metadata.get('metadata', {}).get('doc_id', 'unknown'),
#                     chunk_order_index=metadata.get('chunk', {}).get('chunk_order_index', 0),
#                     source=metadata.get('chunk', {}).get('source')
#                 ))

#             return SearchResponse(
#                 entity_id=request.entity_id,
#                 query=request.query,
#                 results=results,
#                 total=len(results)
#             )

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.error(f"Error in search: {e}")
#             raise HTTPException(status_code=500, detail=str(e))
#         finally:
#             decrement_active_tasks("search")
#             logger.info(f"Completed search request (active: {get_active_tasks_count('search')}/{SEARCH_MAX_WORKERS}, total: {get_active_tasks_count()})")


# ============================================================================
# Knowledge Graph Endpoints
# ============================================================================

@app.get("/api/knowledge-graph", tags=["Knowledge Graph"])
async def get_knowledge_graph(request: KnowledgeGraphRequest):
    """
    Get knowledge graph for specified entities

    Returns all chunks (nodes) and their relationships for the given entities.
    Relationships are constructed between sequential chunks in the same document.

    Args:
        entity_ids: List of entity IDs to include in the graph

    Returns:
        KnowledgeGraphResponse with nodes, relationships, and statistics
    """
    try:
        return task_manager.get_knowledge_graph(request.entity_ids)
    except ValueError as ve:
        raise HTTPException(400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error generating knowledge graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Request Tracking & Cost Reporting Endpoints
# ============================================================================

@app.get("/api/tasks/{task_id}/cost", tags=["Tracking"])
async def get_task_cost(task_id: str):
    return {"message": "Not implemented yet"}


@app.get("/api/cost-report", tags=["Tracking"])
async def get_cost_report(entity_id: Optional[str] = None, session_id: Optional[str] = None):
    return {"message": "Not implemented yet"}


# ============================================================================
# Health & Info Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint"""
    try:
        entities = task_manager.list_entities()
        total_docs = sum(entity.get("documents_count", 0) for entity in entities)

        return HealthResponse(
            status="healthy",
            version="1.0.0",
            entities_loaded=len(entities),
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


# @app.get("/api/status/workers", tags=["System"])
# async def worker_status() -> Dict[str, Any]:
#     """Get current concurrency status with breakdown by operation type"""
#     chat_active = get_active_tasks_count("chat")
#     upload_active = get_active_tasks_count("upload")
#     search_active = get_active_tasks_count("search")

#     # Get upload task statistics
#     with upload_tasks_lock:
#         upload_pending = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.PENDING)
#         upload_processing = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.PROCESSING)
#         upload_completed = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.COMPLETED)
#         upload_failed = sum(1 for task in upload_tasks.values() if task.status == TaskStatus.FAILED)

#     # Get upload executor stats
#     current_upload_workers = upload_executor.get_worker_count()
#     upload_queue_size = upload_executor.get_queue_size()
#     cpu_util = psutil.cpu_percent(interval=0.1)

#     return {
#         "total_active": get_active_tasks_count(),
#         "execution_model": "hybrid_async_dynamic_threadpool",
#         "cpu_utilization": f"{cpu_util:.1f}%",
#         "operations": {
#             "chat": {
#                 "active": chat_active,
#                 "max": CHAT_MAX_WORKERS,
#                 "available": CHAT_MAX_WORKERS - chat_active,
#                 "utilization_pct": round((chat_active / CHAT_MAX_WORKERS) * 100, 2),
#                 "execution": "async_semaphore"
#             },
#             "upload": {
#                 "active": upload_active,
#                 "workers": {
#                     "current": current_upload_workers,
#                     "min": UPLOAD_MIN_WORKERS,
#                     "max": UPLOAD_MAX_WORKERS,
#                 },
#                 "queue_size": upload_queue_size,
#                 "utilization_pct": round((upload_active / current_upload_workers) * 100, 2) if current_upload_workers > 0 else 0,
#                 "execution": "dynamic_threadpool",
#                 "scaling": {
#                     "cpu_threshold": CPU_UTILIZATION_THRESHOLD,
#                     "scale_up_cooldown": SCALE_UP_COOLDOWN,
#                     "scale_down_cooldown": SCALE_DOWN_COOLDOWN
#                 },
#                 "tasks": {
#                     "pending": upload_pending,
#                     "processing": upload_processing,
#                     "completed": upload_completed,
#                     "failed": upload_failed,
#                     "total": len(upload_tasks)
#                 }
#             },
#             "search": {
#                 "active": search_active,
#                 "max": SEARCH_MAX_WORKERS,
#                 "available": SEARCH_MAX_WORKERS - search_active,
#                 "utilization_pct": round((search_active / SEARCH_MAX_WORKERS) * 100, 2),
#                 "execution": "async_semaphore"
#             }
#         }
#     }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint"""
    return {
        "message": "Entity-Scoped RAG API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }
    
# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": str(exc.detail),
            "detail": str(exc)
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
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
        port=8002,
        reload=True,
        log_level="info"
    )
