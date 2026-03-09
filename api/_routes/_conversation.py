"""Conversation management endpoints including WebSocket and SSE streaming."""

from typing import Literal, Optional, Dict, Any, Set
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import WebSocketException
from contextvars import copy_context
from threading import Lock
import json
import asyncio
from starlette.websockets import WebSocketState

from src.core.models._request_models import (
    CreateConversationSessionRequest,
    ListConversationsRequest,
    ConversationRequest,
    ConverseRequest,
    StopConverseRequest,
    ModifyConversationSettingsRequest,
    GetConversationHistoryRequest,
    DownloadConversationFileRequest,
    PreviewConversationFileRequest,
    EditConversationFileRequest,
    BranchConversationRequest,
)
from src.core.models.response_models import ConversationWebSocketTaskResponse
from src.core.models.operation_audit import OperationType
from src.infrastructure.operation_logging import (
    operation_endpoint,
    mark_operation_complete,
    mark_operation_failed,
    get_current_operation,
)
from src.infrastructure.operation_logging.operation_context import current_operation
from src.infrastructure.operation_logging.operation_context import operation_locks
from src.core.models.operation_audit import Operation, TaskStatus
from .._authentication import verify_api_key_header, validate_websocket_api_key
from .._dependencies import task_manager
from src.log_creator import get_file_logger

logger = get_file_logger()
router = APIRouter(prefix="/conversation", tags=["Conversation"])


@router.post("/create", tags=["Conversation"])
@operation_endpoint(OperationType.CREATE_CONVERSATION_SESSION)
async def create_conversation_session(
    request: CreateConversationSessionRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Create a conversation session for a conversation.

    Request body:
        name: Optional name for the conversation session
        user_instructions: Optional instructions/context for the conversation
        settings: Optional conversation settings (enabled_extended_thinking)
    """
    try:
        response = task_manager.conv_manager.create_conversation_session(
            name=request.name,
            kb_ids=request.kb_ids,
            user_instructions=request.user_instructions,
            settings=request.settings,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(
            f"Error creating conversation session: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/list", tags=["Conversation"])
@operation_endpoint(OperationType.LIST_CONVERSATIONS)
async def list_conversations(
    request: ListConversationsRequest, user_id: str = Depends(verify_api_key_header)
):
    """List conversations with optional filtering and projections.

    Request body:
        include_deleted: Whether to include deleted conversations (optional, default: false)
        filters: MongoDB-style filters to apply (e.g., {'kb_ids': ['kb_123']} to filter)
        projections: MongoDB-style projections to specify which fields to include/exclude
    """
    try:
        response = task_manager.conv_manager.list_conversations(
            # include_deleted=request.include_deleted,
            filters=request.filters,
            projections=request.projections,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error listing conversations: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{conversation_id}", tags=["Conversation"])
@operation_endpoint(OperationType.GET_CONVERSATION)
async def get_conversation(conversation_id: str, user_id: str = Depends(verify_api_key_header)):
    try:
        response = task_manager.conv_manager.get_conversation(conversation_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{conversation_id}/converse", tags=["Conversation"], deprecated=True)
@operation_endpoint(OperationType.CONVERSE, auto_complete=False)
async def converse(
    conversation_id: str,
    request: ConversationRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """
    Start a conversation with streaming response using Server-Sent Events (SSE).

    This endpoint streams responses from the agent as they are generated in SSE format.
    Each response is sent as an event with type and data fields.
    """
    try:
        # Capture the operation context and get the current operation
        ctx = copy_context()
        operation = ctx.run(get_current_operation)

        # Create conversation request
        async def response_generator():
            # Set the operation in the current context so all logging calls have access to it
            token = current_operation.set(operation)

            # Create and set a lock for this operation to avoid warnings in update_operation_metadata
            locks = operation_locks.get().copy()
            if operation:
                if operation.get_id() not in locks:
                    locks[operation.get_id()] = Lock()
            locks_token = operation_locks.set(locks)

            try:
                async for response in task_manager.conv_manager.converse(conversation_id, request):
                    # Convert Pydantic models to dict before JSON serialization
                    response_dict = response.model_dump(mode="json", exclude_none=True)
                    # Get event type from response type or use generic name
                    event_type = response_dict.get("type", "message")

                    # Format as Server-Sent Event (SSE)
                    yield f"event: {event_type}\n"
                    yield f"data: {json.dumps(response_dict)}\n\n"

                # Mark operation complete
                mark_operation_complete()
            except Exception as e:
                logger.error(f"Error in conversation stream: {str(e)}", exc_info=True)
                error_response = {
                    "type": "error",
                    "error": "Internal server error!",
                    "content": f"Error during conversation",
                }
                yield f"event: error\n"
                yield f"data: {json.dumps(error_response)}\n\n"

                # Mark operation failed
                mark_operation_failed(str(e))
            finally:
                # Restore the previous operation and locks context
                current_operation.reset(token)
                operation_locks.reset(locks_token)

        return StreamingResponse(
            response_generator(),
            media_type="text/event-stream",
            headers={
                "chat-id": conversation_id,
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    except Exception as e:
        logger.error(f"Error starting conversation: {str(e)}")
        mark_operation_failed(str(e))
        raise HTTPException(status_code=500, detail="Internal server error!")


@router.post("/{conversation_id}/submit-message", tags=["Conversation"])
@operation_endpoint(OperationType.SUBMIT_CONVERSE_MESSAGE, auto_complete=True)
async def submit_converse_message(
    conversation_id: str,
    request: ConversationRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """
    Submit a message to a conversation's processing queue.

    This endpoint queues the message without starting processing.
    Call /process endpoint to begin processing queued messages.

    Request body:
        prompt: User message/question
        parent_message_uuid: Optional parent message UUID for thread
        attachments: Optional file attachments
        personalized_styles: Optional styling preferences
    """
    try:
        task_manager.conv_manager.submit_converse_message(conversation_id, request)
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Message queued for conversation {conversation_id}",
                "conversation_id": conversation_id,
            },
        )
    except Exception as e:
        logger.error(f"Error submitting message: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{conversation_id}/process", tags=["Conversation"])
@operation_endpoint(OperationType.PROCESS_CONVERSE_MESSAGES, auto_complete=False)
async def process_queued_messages(
    conversation_id: str,
    user_id: str = Depends(verify_api_key_header),
):
    """
    Start processing queued messages for a conversation using Server-Sent Events (SSE).

    This endpoint processes all messages that have been queued via /submit-message.
    Each response is sent as an event with type and data fields.
    """
    try:
        # Capture the operation context and get the current operation
        ctx = copy_context()
        operation = ctx.run(get_current_operation)

        # Create conversation request
        async def response_generator():
            # Set the operation in the current context so all logging calls have access to it
            token = current_operation.set(operation)

            # Create and set a lock for this operation to avoid warnings in update_operation_metadata
            locks = operation_locks.get().copy()
            if operation:
                if operation.get_id() not in locks:
                    locks[operation.get_id()] = Lock()
            locks_token = operation_locks.set(locks)

            try:
                async for response in task_manager.conv_manager.start_processing_messages(
                    conversation_id
                ):
                    # Convert Pydantic models to dict before JSON serialization
                    response_dict = response.model_dump(mode="json", exclude_none=True)
                    # Get event type from response type or use generic name
                    event_type = response_dict.get("type", "message")

                    # Format as Server-Sent Event (SSE)
                    yield f"event: {event_type}\n"
                    yield f"data: {json.dumps(response_dict)}\n\n"

                # Mark operation complete
                mark_operation_complete()
            except Exception as e:
                logger.error(f"Error in processing stream: {str(e)}", exc_info=True)
                error_response = {
                    "type": "error",
                    "error": "Internal server error!",
                    "content": f"Error during message processing",
                }
                yield f"event: error\n"
                yield f"data: {json.dumps(error_response)}\n\n"

                # Mark operation failed
                mark_operation_failed(str(e))
            finally:
                # Restore the previous operation and locks context
                current_operation.reset(token)
                operation_locks.reset(locks_token)

        return StreamingResponse(
            response_generator(),
            media_type="text/event-stream",
            headers={
                "chat-id": conversation_id,
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    except Exception as e:
        logger.error(f"Error starting message processing: {str(e)}")
        mark_operation_failed(str(e))
        raise HTTPException(status_code=500, detail="Internal server error!")


@router.websocket("/ws/{conversation_id}")
@operation_endpoint(OperationType.CONVERSE_WEBSOCKET)
async def websocket_converse(
    websocket: WebSocket, conversation_id: str, user_id: str = Depends(validate_websocket_api_key)
):
    """WebSocket endpoint for concurrent conversation operations.

    Supports parallel handling of multiple operation types:
    - submit: Queue conversation messages
    - stop: Stop streaming responses
    - get_conversation: Get conversation details
    - list_conversations: List conversations
    - get_history: Get conversation history
    - list_files: List conversation files
    - download_file: Download conversation file
    - preview_file: Preview conversation file
    - edit_file: Edit conversation file
    - modify_settings: Modify conversation settings
    - delete: Delete conversation
    - ping: Keep connection alive

    Each operation is processed concurrently with task_id for tracking.
    """
    # Validate API key before accepting the connection
    try:
        logger.info(f"[ws] Starting WebSocket connection for conversation: {conversation_id}")
        user_id = await validate_websocket_api_key(websocket)
        logger.info(
            f"[ws] WebSocket authenticated for user: {user_id}, conversation: {conversation_id}"
        )
    except WebSocketException as e:
        logger.warning(
            f"[ws] WebSocket authentication failed for conversation: {conversation_id}: {str(e)}"
        )
        raise

    logger.info(f"[ws] Accepting WebSocket connection")
    await websocket.accept()
    logger.info(f"[ws] WebSocket connection accepted")

    # Set up operation context with user_id for this WebSocket connection
    # This ensures all operations within the WebSocket have access to the user_id for database queries
    ws_operation = Operation(
        user_id=user_id,
        operation_type=OperationType.CONVERSE,
        status=TaskStatus.PROCESSING,
        description=f"WebSocket connection for conversation {conversation_id}",
    )
    operation_token = current_operation.set(ws_operation)
    logger.info(f"[ws] Operation context set with user_id: {user_id}")

    active_tasks: Set[asyncio.Task[None]] = set()
    stream_task: Optional[asyncio.Task[None]] = None
    message_count = 0

    async def stream_responses():
        try:
            logger.info(
                f"[stream] Starting to process messages for conversation: {conversation_id}"
            )
            async for resp in task_manager.conv_manager.start_processing_messages(conversation_id):
                resp_dict = resp.model_dump(mode="json", exclude_none=True)
                logger.debug(f"[stream] Sending response: {resp_dict.get('type', 'unknown')}")
                await websocket.send_json(resp_dict)
            logger.info(
                f"[stream] Completed processing messages for conversation: {conversation_id}"
            )
        except WebSocketDisconnect:
            logger.info(
                f"[stream] WebSocket disconnected, stopping message streaming for conversation: {conversation_id}"
            )
        except Exception as e:
            logger.error(f"[stream] Streaming error: {str(e)}", exc_info=True)

    async def handle_operation(
        task_id: str,
        type: Literal[
            "ping-pong",
            "submit",
            "get_conversation",
            "get_history",
            "list_files",
            "modify_settings",
            "edit_file",
            "preview_file",
        ],
        request: Dict[str, Any],
    ):
        """Handle a single WebSocket operation concurrently"""
        # Validate operation type
        valid_types = {
            "ping-pong",
            "submit",
            "get_conversation",
            "get_history",
            "list_files",
            "modify_settings",
            "edit_file",
            "preview_file",
        }

        if type not in valid_types:
            logger.warning(f"[handle_op] Invalid operation type: {type}")
            try:
                error_response = ConversationWebSocketTaskResponse(
                    task_id=task_id,
                    type="ping-pong",  # Use a safe default type for error response
                    success=False,
                    message=f"Unknown operation type: {type}",
                )
                await websocket.send_json(error_response.model_dump(mode="json", exclude_none=True))
            except Exception as e:
                logger.error(f"[handle_op] Failed to send invalid type error response: {str(e)}")
            return

        try:
            response = ConversationWebSocketTaskResponse(
                task_id=task_id, type=type, success=False, message="Operation processing..."
            )

            if type == "submit":
                # Submit message to conversation queue
                nonlocal stream_task
                request_data = request.copy()
                request_type = request_data.get("request_type", "converse")

                logger.info(
                    f"[submit] task_id={task_id}, request_type={request_type}, request_data={request_data}"
                )

                if request_type == "converse":
                    logger.info(f"[submit] Creating ConverseRequest with data: {request_data}")
                    conv_request: ConversationRequest = ConverseRequest(**request_data)
                else:
                    logger.info(f"[submit] Creating StopConverseRequest with data: {request_data}")
                    conv_request = StopConverseRequest(**request_data)

                logger.info(f"[submit] Submitting message for conversation: {conversation_id}")
                task_manager.conv_manager.submit_converse_message(conversation_id, conv_request)
                logger.info(f"[submit] Message submitted successfully")

                # Start streaming if not already running
                if stream_task is None or stream_task.done():
                    logger.info(f"[submit] Starting new streaming task")
                    stream_task = asyncio.create_task(stream_responses())
                    response.success = True
                    response.message = "Message queued and streaming started"
                    logger.info(f"[submit] Streaming task started, responding to client")
                else:
                    response.success = True
                    response.message = "Message queued - streaming in progress"
                    logger.info(f"[submit] Streaming already in progress, queued message")

            elif type == "get_conversation":
                # Get conversation details
                get_conv_response = task_manager.conv_manager.get_conversation(conversation_id)
                response.success = get_conv_response.success
                response.data = (
                    get_conv_response.model_dump(mode="json", exclude_none=True)
                    if get_conv_response.success
                    else None
                )
                response.message = get_conv_response.message

            elif type == "get_history":
                # Get conversation history
                history_response = task_manager.conv_manager.get_conversation_history(
                    conversation_id,
                    offset=request.get("offset", 0),
                    limit=request.get("limit", 100),
                )
                response.success = history_response.success
                response.data = (
                    history_response.model_dump(mode="json", exclude_none=True)
                    if history_response.success
                    else None
                )
                response.message = history_response.message

            elif type == "list_files":
                # List conversation files
                files_response = task_manager.conv_manager.list_conversation_files(conversation_id)
                response.success = files_response.success
                response.data = (
                    files_response.model_dump(mode="json", exclude_none=True)
                    if files_response.success
                    else None
                )
                response.message = files_response.message

            elif type == "modify_settings":
                # Modify conversation settings
                settings_response = task_manager.conv_manager.modify_conversation_settings(
                    conversation_id,
                    name=request.get("name"),
                    user_instructions=request.get("user_instructions"),
                    settings_updates=request.get("settings"),
                )
                response.success = settings_response.success
                response.data = (
                    settings_response.model_dump(mode="json", exclude_none=True)
                    if settings_response.success
                    else None
                )
                response.message = settings_response.message

            elif type == "edit_file":
                # Edit file - returns EditConversationFileResponse (Pydantic model)
                try:
                    file_path = request.get("file_path")
                    new_content = request.get("new_content")
                    if not file_path or new_content is None:
                        response.success = False
                        response.message = "file_path and new_content are required"
                    else:
                        edit_response = task_manager.conv_manager.edit_conversation_file(
                            conversation_id, file_path=file_path, new_content=new_content
                        )
                        response.success = edit_response.success
                        response.data = (
                            edit_response.model_dump(mode="json", exclude_none=True)
                            if edit_response.success
                            else None
                        )
                        response.message = edit_response.message
                except Exception as e:
                    response.success = False
                    response.message = f"Failed to edit file: {str(e)}"

            elif type == "preview_file":
                # Preview file - returns PreviewFileResponse (Pydantic model)
                try:
                    file_path = request.get("file_path")
                    if not file_path:
                        response.success = False
                        response.message = "file_path is required"
                    else:
                        file_preview = task_manager.conv_manager.preview_conversation_file(
                            conversation_id, file_path=file_path, version=request.get("version")
                        )
                        response.success = True
                        response.data = file_preview.model_dump(mode="json", exclude_none=True)
                        response.message = "File preview retrieved successfully"
                except ValueError as e:
                    response.success = False
                    response.message = f"File not found: {str(e)}"
                except Exception as e:
                    response.success = False
                    response.message = f"Failed to preview file: {str(e)}"

            elif type == "ping-pong":
                # Ping/pong to keep connection alive
                if websocket.client_state == WebSocketState.CONNECTED:
                    response.success = True
                    response.message = "ping-pong"
                else:
                    response.success = False

            else:
                response.message = f"Unknown operation type: {type}"

            # Send response
            await websocket.send_json(response.model_dump(mode="json", exclude_none=True))

        except Exception as e:
            logger.error(
                f"[handle_op] Error handling operation {task_id} ({type}): {str(e)}", exc_info=True
            )
            try:
                error_response = ConversationWebSocketTaskResponse(
                    task_id=task_id, type=type, success=False, message=f"Operation failed: {str(e)}"
                )
                logger.error(f"[handle_op] Sending error response: {error_response.message}")
                await websocket.send_json(error_response.model_dump(mode="json", exclude_none=True))
            except Exception as send_error:
                logger.error(f"[handle_op] Failed to send error response: {str(send_error)}")

    try:
        # Handle incoming messages concurrently
        while True:
            try:
                # Receive and parse JSON message
                data = await websocket.receive_json()
                message_count += 1
                logger.info(f"[ws] Received WebSocket message #{message_count}: {data}")

                try:
                    # Parse operation request
                    task_id = data.get("task_id")
                    type = data.get("type")
                    request_data = data.get("request", {})

                    logger.info(
                        f"[ws] Parsed: task_id={task_id}, type={type}, request_data keys={list(request_data.keys())}"
                    )

                    if not task_id or not type:
                        logger.warning(f"[ws] Missing task_id or type in message")
                        await websocket.send_json(
                            {
                                "task_id": task_id,
                                "type": type,
                                "success": False,
                                "message": "Missing task_id or type",
                            }
                        )
                        continue

                    # Create task and keep reference to avoid "Task exception was never retrieved"
                    logger.info(f"[ws] Creating task for operation: {type}")
                    task = asyncio.create_task(handle_operation(task_id, type, request_data))
                    active_tasks.add(task)

                    # Clean up completed tasks
                    active_tasks = {t for t in active_tasks if not t.done()}

                except Exception as e:
                    logger.error(
                        f"[ws] Error parsing message #{message_count}: {str(e)}", exc_info=True
                    )
                    try:
                        await websocket.send_json(
                            {"success": False, "message": f"Message parsing error: {str(e)}"}
                        )
                    except Exception as send_error:
                        logger.error(f"[ws] Failed to send parse error response: {str(send_error)}")

            except json.JSONDecodeError as e:
                # Handle malformed JSON gracefully
                message_count += 1
                logger.warning(f"[ws] Malformed JSON in message #{message_count}: {str(e)}")
                try:
                    await websocket.send_json(
                        {"success": False, "message": f"JSON parsing error: {str(e)}"}
                    )
                except Exception as send_error:
                    logger.error(f"[ws] Failed to send JSON error response: {str(send_error)}")
            except WebSocketDisconnect:
                raise  # Re-raise to be caught by outer handler
            except Exception as e:
                # Handle other receive errors
                logger.error(f"[ws] Error receiving message: {str(e)}", exc_info=True)
                try:
                    await websocket.send_json(
                        {"success": False, "message": f"Receive error: {str(e)}"}
                    )
                except Exception:
                    pass

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected for conversation: {conversation_id}")
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint: {str(e)}", exc_info=True)

    finally:
        # Reset operation context
        try:
            current_operation.reset(operation_token)
            logger.info(f"[ws] Operation context reset")
        except Exception as e:
            logger.error(f"[ws] Error resetting operation context: {str(e)}")

        # Clean up all active tasks
        for task in active_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop streaming task if running
        if stream_task and not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass

        # Close websocket if still connected
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception as e:
            logger.debug(f"Error closing WebSocket: {str(e)}")


@router.get("/{conversation_id}/files", tags=["Conversation"])
@operation_endpoint(OperationType.LIST_CONVERSATION_FILES)
async def list_conversation_files(
    conversation_id: str, user_id: str = Depends(verify_api_key_header)
):
    """List files associated with a conversation"""
    try:
        response = task_manager.conv_manager.list_conversation_files(conversation_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{chat_id}/download", tags=["Conversation"])
@operation_endpoint(OperationType.DOWNLOAD_CONVERSATION_FILE)
async def download_conversation_file(
    chat_id: str,
    request: DownloadConversationFileRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Download a file from a conversation (chunked binary streaming).

    Streams file content in 8KB chunks for network efficiency and better UX.
    Version parameter is optional - if not specified, downloads the latest version.
    """
    try:
        file_content, filename, content_type = task_manager.conv_manager.download_conversation_file(
            chat_id, request.file_path, version=request.version
        )

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
    except Exception as e:
        logger.error(f"Error downloading conversation file: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{chat_id}/preview", tags=["Conversation"])
@operation_endpoint(OperationType.PREVIEW_CONVERSATION_FILE)
async def preview_conversation_file(
    chat_id: str,
    request: PreviewConversationFileRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Preview a file from a conversation.

    Returns only three types of preview content:
    - Images (PNG, JPG, GIF, etc.): Base64 encoded image
    - PDFs: Base64 encoded PDF
    - Markdown: Text content in markdown format (with code blocks for code files)

    Conversions performed:
    - Code files (.py, .js, etc.) → Markdown with language-specific code blocks
    - Plain text files → Markdown format
    - Office documents (DOCX, XLSX, PPTX) → PDF format (base64 encoded)

    Response includes both original_content_type and preview_content_type fields.
    Version parameter is optional - if not specified, previews the latest version.
    """
    try:
        response = task_manager.conv_manager.preview_conversation_file(
            chat_id, request.file_path, version=request.version
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{conversation_id}/history", tags=["Conversation"])
@operation_endpoint(OperationType.GET_CONVERSATION_HISTORY)
async def get_conversation_history(
    conversation_id: str,
    request: GetConversationHistoryRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Get conversation history"""
    try:
        response = task_manager.conv_manager.get_conversation_history(
            conversation_id, request.offset, request.limit
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.delete("/{chat_id}", tags=["Conversation"])
@operation_endpoint(OperationType.DELETE_CONVERSATION)
async def delete_conversation(chat_id: str, user_id: str = Depends(verify_api_key_header)):
    """Delete a conversation"""
    try:
        response = await task_manager.conv_manager.delete_conversation(chat_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.put("/conversation/{conversation_id}/settings", tags=["Conversation"])
@operation_endpoint(OperationType.UPDATE_CONVERSATION)
async def modify_conversation_settings(
    conversation_id: str,
    request: ModifyConversationSettingsRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Modify conversation settings including name, user instructions, and individual settings keys.

    Request Body:
        name: Optional new conversation name
        user_instructions: Optional new user instructions
        settings: Optional dict with individual settings keys to update
                (e.g., {"enabled_extended_thinking": true})
    """
    try:
        response = task_manager.conv_manager.modify_conversation_settings(
            conversation_id=conversation_id,
            name=request.name,
            user_instructions=request.user_instructions,
            settings_updates=request.settings,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error modifying conversation settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{conversation_id}/edit-file", tags=["Conversation"])
@operation_endpoint(OperationType.EDIT_FILE)
async def edit_conversation_file(
    conversation_id: str,
    request: EditConversationFileRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Edit a file in a conversation's working directory.

    Path Args:
        conversation_id: Conversation ID

    Request body:
        file_path: Path to the file to edit (relative to conversation working directory)
        new_content: New content for the file

    Returns:
        EditConversationFileResponse with file metadata if successful
    """
    try:
        response = task_manager.conv_manager.edit_conversation_file(
            conversation_id=conversation_id,
            file_path=request.file_path,
            new_content=request.new_content,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error editing conversation file: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{conversation_id}/branch", tags=["Conversation"])
@operation_endpoint(OperationType.BRANCH_CONVERSATION_SESSION)
async def branch_conversation(
    conversation_id: str,
    request: BranchConversationRequest,
    user_id: str = Depends(verify_api_key_header),
):
    """Branch a conversation from a specific message.

    Creates a new conversation that branches from the original conversation at a specific message point.
    The new conversation will have the same settings as the original, with a tree_id linking to the parent.

    Path Args:
        conversation_id: ID of the conversation to branch from

    Request body:
        message_id: ID of the message to branch from

    Returns:
        Response with new_conversation_id, parent_conversation_id, and message_id
    """
    try:
        response = task_manager.conv_manager.branch_conversation(
            conversation_id=conversation_id,
            message_id=request.message_id,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))
    except Exception as e:
        logger.error(f"Error branching conversation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
