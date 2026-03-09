"""Document management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.models._request_models import (
    GetDocumentsBatchRequest,
    DownloadDocumentsBatchRequest,
    GetDocumentPresignedUrlRequest,
)
from src.core.models.response_models import GetDocumentPresignedUrlResponse
from src.core.models.operation_audit import OperationType
from src.infrastructure.operation_logging import operation_endpoint
from .._authentication import verify_api_key_header
from .._dependencies import task_manager
from src.log_creator import get_file_logger

logger = get_file_logger()
router = APIRouter(prefix="/documents", tags=["Documents"])


@router.get("/{doc_id}", tags=["Documents"])
@operation_endpoint(OperationType.GET_DOCUMENT)
async def get_document(doc_id: str, user_id: str = Depends(verify_api_key_header)):
    """Get document metadata by ID.

    Args:
        doc_id: Document ID

    Returns:
        Document metadata including name, type, size, source, upload date
    """
    try:
        response = task_manager.kb_manager.get_document(doc_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{doc_id}/download", tags=["Documents"])
@operation_endpoint(OperationType.DOWNLOAD_DOCUMENT)
async def download_document(doc_id: str, user_id: str = Depends(verify_api_key_header)):
    """Download document file content (chunked binary streaming).

    Args:
        doc_id: Document ID

    Returns:
        File content with appropriate content-type header (streamed in 8KB chunks)
    """
    try:
        file_content, filename, content_type = task_manager.kb_manager.download_document(doc_id)

        # Chunked binary streaming generator for network efficiency
        async def chunk_generator(data: bytes, chunk_size: int = 8192):
            """Stream binary data in 8KB chunks for better network transfer"""
            for i in range(0, len(data), chunk_size):
                yield data[i : i + chunk_size]

        return StreamingResponse(
            chunk_generator(file_content),
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Length": str(len(file_content)),
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IOError as e:
        logger.error(f"Error downloading document {doc_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to download document")
    except Exception as e:
        logger.error(f"Error downloading document {doc_id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{doc_id}/presigned-url", tags=["Documents"])
@operation_endpoint(OperationType.GET_DOCUMENT)
async def get_document_presigned_url(
    doc_id: str,
    request: GetDocumentPresignedUrlRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Get a presigned URL for direct access to document in S3.

    Args:
        doc_id: Document ID
        request: Request body containing expiration and inline settings

    Returns:
        Presigned URL that can be used for direct S3 access without authentication
    """
    try:
        # Limit expiration to 1 hour max
        expiration = request.expiration
        if expiration > 3600:
            expiration = 3600

        presigned_url, _ = task_manager.kb_manager.get_document_presigned_url(
            doc_id, expiration, inline=request.inline
        )

        response = GetDocumentPresignedUrlResponse(
            doc_id=doc_id,
            presigned_url=presigned_url,
            expires_in_seconds=expiration,
            message="Presigned URL generated successfully",
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IOError as e:
        logger.error(f"Error generating presigned URL for {doc_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating presigned URL for {doc_id}: {str(e)}")
        raise HTTPException(status_code=400, detail="Failed to generate presigned URL")


@router.post("/search", tags=["Documents"])
@operation_endpoint(OperationType.GET_DOCUMENTS)
async def search_documents(
    request: GetDocumentsBatchRequest, user_id: str = Depends(verify_api_key_header)
):
    """Search documents with MongoDB-style filters and projections.

    Request body:
        filters: MongoDB-style filters to retrieve documents (required)
                 Examples:
                 - Batch retrieval: {'_id': {'$in': ['doc_1', 'doc_2']}}
                 - Filter by type: {'content_type': 'pdf'}
                 - Complex: {'kb_id': 'kb_123', 'content_type': {'$in': ['pdf', 'txt']}}
        projections: MongoDB-style projections to specify which fields to include/exclude (optional)

    Returns:
        List of documents matching the filters with specified projections
    """
    try:
        if not request.filters:
            raise ValueError("Filters are required to search documents")

        response = task_manager.kb_manager.get_documents_batch(
            filters=request.filters, projections=request.projections
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error searching documents: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/batch/download", tags=["Documents"])
@operation_endpoint(OperationType.DOWNLOAD_DOCUMENTS)
async def download_documents_batch(
    request: DownloadDocumentsBatchRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Download multiple documents as a zip file (chunked binary streaming).

    Request body:
        filters: MongoDB-style filters to select documents to download (required)
                 Examples:
                 - Batch retrieval: {'_id': {'$in': ['doc_1', 'doc_2']}}
                 - Filter by type: {'content_type': 'pdf'}
                 - Complex: {'kb_id': 'kb_123', 'content_type': {'$in': ['pdf', 'txt']}}

    Returns:
        Zip file containing all requested documents (streamed in 8KB chunks for network efficiency)
    """
    try:
        if not request.filters:
            raise ValueError("Filters are required to download documents")

        zip_content, filename = task_manager.kb_manager.download_documents_batch(request.filters)

        # Chunked binary streaming generator for network efficiency
        # Yields data in 8KB chunks instead of sending entire file at once
        async def chunk_generator(data: bytes, chunk_size: int = 8192):
            """Stream binary data in 8KB chunks for better network transfer"""
            for i in range(0, len(data), chunk_size):
                yield data[i : i + chunk_size]

        return StreamingResponse(
            chunk_generator(zip_content),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Length": str(len(zip_content)),
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error downloading documents batch: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
