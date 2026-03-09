"""Knowledge base management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from typing import List, Dict, Any
import mimetypes
import magic

from src.core.models._request_models import (
    CreateKnowledgeBaseRequest,
    ListKnowledgeBasesRequest,
    ModifyKnowledgeBaseRequest,
    DeleteDocumentsFromKBRequest,
    UploadChunksRequest,
)
from src.core.models.core_models import Doc
from src.core.models.operation_audit import OperationType
from src.infrastructure.operation_logging import operation_endpoint
from src.log_creator import get_file_logger

from .._authentication import verify_api_key_header
from .._dependencies import task_manager

logger = get_file_logger()
router = APIRouter(tags=["Knowledge Base"])


@router.post("/knowledge-base/create", tags=["Knowledge Base"])
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_knowledge_base(
    request: CreateKnowledgeBaseRequest, user_id: str = Depends(verify_api_key_header)
):
    """
    Create a new knowledge base

    Args:
        title: Knowledge base title
        metadata: Optional metadata dictionary
    """
    try:
        response = task_manager.kb_manager.create_knowledge_base(
            title=request.title,
            description=request.description,
            metadata=request.metadata or {},
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/knowledge-base/{kb_id}", tags=["Knowledge Base"])
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_knowledge_base(kb_id: str, user_id: str = Depends(verify_api_key_header)):
    """Get knowledge base information by ID"""
    try:
        response = task_manager.kb_manager.get_knowledge_base(kb_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/knowledge-bases/list", tags=["Knowledge Base"])
@operation_endpoint(OperationType.LIST_KNOWLEDGE_BASE)
async def list_knowledge_bases(
    request: ListKnowledgeBasesRequest, user_id: str = Depends(verify_api_key_header)
):
    """List knowledge bases with optional filtering and projections.

    Request body:
        include_deleted: If true, includes deleted KBs in the response (default: false)
        filters: MongoDB-style filters to apply to knowledge bases
        projections: MongoDB-style projections to specify which fields to include/exclude
    """
    try:
        response = task_manager.kb_manager.list_knowledge_bases(
            filters=request.filters,
            projections=request.projections,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error listing knowledge bases: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/knowledge-base/{kb_id}/upload", tags=["Knowledge Base"])
@operation_endpoint(OperationType.UPLOAD_DOCS_TO_KNOWLEDGE_BASE, auto_complete=False)
async def upload_documents(
    kb_id: str,
    files: List[UploadFile] = File(..., description="Files to upload (PDF, DOCX, TXT, etc.)"),
    user_id: str = Depends(verify_api_key_header),
):
    """
    Upload documents to a knowledge base

    Args:
        kb_id: Knowledge base ID
        files: List of files to upload (PDF, DOCX, TXT, Markdown, JSON, etc.)
    """
    try:
        docs: List[Doc] = []
        for file in files:
            content = await file.read()
            filename = file.filename or "temp.txt"

            # Detect proper MIME type from file content and filename
            content_type = file.content_type

            # Try to detect from file content using magic
            try:
                detected_mime = magic.from_buffer(content, mime=True)
                if detected_mime and detected_mime != "application/octet-stream":
                    content_type = detected_mime
            except (ImportError, Exception):
                pass

            # If still not detected or is octet-stream, try filename-based detection
            if not content_type or content_type == "application/octet-stream":
                guessed_mime = mimetypes.guess_type(filename)[0]
                if guessed_mime:
                    content_type = guessed_mime

            # Fallback to explicit mapping for common types
            if not content_type or content_type == "application/octet-stream":
                lower_name = filename.lower()
                if lower_name.endswith(".pdf"):
                    content_type = "application/pdf"
                elif lower_name.endswith((".docx", ".doc")):
                    content_type = (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                elif lower_name.endswith((".xlsx", ".xls")):
                    content_type = (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                elif lower_name.endswith((".txt", ".text")):
                    content_type = "text/plain"
                elif lower_name.endswith((".md", ".markdown")):
                    content_type = "text/markdown"
                elif lower_name.endswith((".json",)):
                    content_type = "application/json"

            # Final fallback only if all detection failed
            if not content_type:
                content_type = "application/octet-stream"

            doc = Doc(
                doc_name=filename,
                content_type=content_type,
                content=content,
                source="upload",
            )
            docs.append(doc)

        response = task_manager.kb_manager.upload_docs_to_knowledge_base(kb_id, docs)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.put("/knowledge-base/{kb_id}", tags=["Knowledge Base"])
@operation_endpoint(OperationType.UPDATE_KNOWLEDGE_BASE)
async def modify_knowledge_base(
    kb_id: str,
    request: ModifyKnowledgeBaseRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Modify knowledge base metadata, and other properties"""
    try:
        kwargs: Dict[str, Any] = {}
        if request.title is not None:
            kwargs["title"] = request.title
        if request.metadata is not None:
            kwargs["metadata"] = request.metadata
        if request.metadata_updates is not None:
            kwargs["metadata_updates"] = request.metadata_updates

        response = task_manager.kb_manager.modify_knowledge_base(kb_id, **kwargs)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error modifying knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.delete("/knowledge-base/{kb_id}", tags=["Knowledge Base"])
@operation_endpoint(OperationType.DELETE_KNOWLEDGE_BASE)
async def delete_knowledge_base(kb_id: str, user_id: str = Depends(verify_api_key_header)):
    """Delete a knowledge base and all associated resources"""
    try:
        response = task_manager.kb_manager.delete_knowledge_base(kb_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.delete("/knowledge-base/{kb_id}/documents", tags=["Knowledge Base"])
@operation_endpoint(OperationType.DELETE_KNOWLEDGE_BASE_DOCS)
async def delete_documents(
    kb_id: str,
    request: DeleteDocumentsFromKBRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Delete specific documents from a knowledge base.

    Path Args:
        kb_id: Knowledge base ID

    Request body:
        doc_ids: List of document IDs to delete
    """
    try:
        if not request.doc_ids:
            raise ValueError("At least one document ID is required")

        response = task_manager.kb_manager.delete_docs_from_knowledge_base(kb_id, request.doc_ids)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error deleting documents from knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/knowledge-base/{kb_id}/upload-chunks", tags=["Knowledge Base"])
@operation_endpoint(OperationType.UPLOAD_CHUNKS_TO_KNOWLEDGE_BASE, auto_complete=False)
async def upload_chunks(
    kb_id: str,
    request: UploadChunksRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """
    Upload pre-chunked data directly to a knowledge base.
    Useful for bulk imports or external chunk sources.

    Path Args:
        kb_id: Knowledge base ID

    Request body:
        chunks: List of pre-chunked Chunk objects
    """
    try:
        if not request.chunks:
            raise ValueError("At least one chunk is required")

        response = task_manager.kb_manager.upload_chunks_to_knowledge_base(kb_id, request.chunks)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error uploading chunks to knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
