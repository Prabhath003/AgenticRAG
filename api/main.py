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
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

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
    HealthResponse, ErrorResponse
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

entities_storage = JSONStorage(str(STORAGE_DIR))
chat_sessions_storage = JSONStorage(str(STORAGE_DIR))

ENTITIES_COLLECTION = "entities"
CHAT_SESSIONS_COLLECTION = "chat_sessions"

# In-memory cache for session agents (reconstructed on startup)
session_agents: Dict[str, ResearchAgent] = {}

# Upload directory
UPLOAD_DIR = Path(Config.DATA_DIR) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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

@app.post("/api/entities/{entity_id}/files", response_model=FileUploadResponse, tags=["Files"])
async def upload_file(
    entity_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    source: Optional[str] = Form(None)
):
    """
    Upload a file to an entity

    Args:
        entity_id: Entity identifier
        file: File to upload
        description: Optional description
        source: Optional source identifier for chunking (e.g., 'company_123/financials/report.pdf')

    Returns doc_id for the uploaded file
    """
    try:
        # Check if entity exists
        entity_data = get_entity_from_storage(entity_id)
        if not entity_data:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Save uploaded file
        file_path = UPLOAD_DIR / entity_id / file.filename
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Index the file
        metadata = {
            "description": description,
            "uploaded_at": datetime.utcnow().isoformat(),
            "original_filename": file.filename
        }

        # Add source to metadata if provided (will be used by file processor)
        if source:
            metadata["source"] = source

        result = index_document_entity_scoped(
            entity_id=entity_id,
            file_path=str(file_path),
            metadata=metadata
        )

        if not result:
            raise HTTPException(status_code=500, detail="Failed to index document")

        # Update entity documents list
        entity_data["documents"].append({
            "doc_id": result["doc_id"],
            "filename": file.filename,
            "file_path": str(file_path),
            "uploaded_at": datetime.utcnow().isoformat(),
            "source": source or str(file_path)
        })
        save_entity(entity_data)

        logger.info(f"Uploaded file {file.filename} to entity {entity_id}, doc_id: {result['doc_id']}, source: {source or 'default'}")

        return FileUploadResponse(
            success=True,
            doc_id=result["doc_id"],
            entity_id=entity_id,
            filename=file.filename,
            chunks_count=result.get("chunks_count", 0),
            is_duplicate=result.get("is_duplicate", False),
            message=f"File uploaded and indexed successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
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
    """
    try:
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


# ============================================================================
# Search Endpoints
# ============================================================================

@app.post("/api/search", response_model=SearchResponse, tags=["Search"])
async def search(request: SearchRequest):
    """
    Search within an entity's documents

    Fast entity-scoped search (10-100x faster than global search)
    """
    try:
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
