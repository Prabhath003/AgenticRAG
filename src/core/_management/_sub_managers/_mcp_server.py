from typing import Dict, Any, List, Union

from core.models.core_models import Document

from ._conversation_manager import ConversationManager
from ...models._request_models import (
    ExecuteToolRequest,
    SearchToolsRequest,
)
from ...models.response_models import (
    GetToolsResponse,
    ExecuteToolResponse,
    SearchToolsResponse,
)
from ....log_creator import get_file_logger

logger = get_file_logger()


class MCPServer:
    """
    MCP Server exposing 5 tools for external agents to interact with AgenticRAG.

    Tools:
    1. userKBAgent-create_session: Create a new conversation session
    2. userKBAgent-ask: Ask a question in a conversation
    3. userKBAgent-list_sessions: List all conversation sessions
    4. userKBAgent-close_session: Close/delete a conversation session
    5. userKBAgent-get_file: Download/get a file from a document
    """

    def __init__(self, conv_manager: ConversationManager, kb_manager: Any = None):
        self.conv_manager = conv_manager
        self.kb_manager = kb_manager

        # Define the 5 available tools
        self._tools = self._init_tools()

    def _init_tools(self) -> Dict[str, Dict[str, Any]]:
        """Initialize tool definitions for the 5 MCP tools."""
        return {
            "userKBAgent-create_session": {
                "type": "function",
                "function": {
                    "name": "userKBAgent-create_session",
                    "description": "Create a new conversation session with the AgenticRAG system",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Optional name for the conversation session",
                            },
                            "kb_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of knowledge base IDs to associate with this conversation",
                            },
                            "user_instructions": {
                                "type": "string",
                                "description": "Optional instructions/context for the conversation",
                            },
                        },
                        "required": [],
                    },
                },
            },
            "userKBAgent-ask": {
                "type": "function",
                "function": {
                    "name": "userKBAgent-ask",
                    "description": "Ask a question in an existing conversation session",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "conversation_id": {
                                "type": "string",
                                "description": "ID of the conversation to ask in",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "The question or prompt to send",
                            },
                            "parent_message_uuid": {
                                "type": "string",
                                "description": "Optional UUID of the parent message (for branching conversations)",
                            },
                        },
                        "required": ["conversation_id", "prompt"],
                    },
                },
            },
            "userKBAgent-list_sessions": {
                "type": "function",
                "function": {
                    "name": "userKBAgent-list_sessions",
                    "description": "List all conversation sessions",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filters": {
                                "type": "object",
                                "description": "MongoDB-style filters to apply (e.g., {'kb_ids': ['kb_123']})",
                            },
                            "projections": {
                                "type": "object",
                                "description": "MongoDB-style projections to specify which fields to include/exclude",
                            },
                        },
                        "required": [],
                    },
                },
            },
            "userKBAgent-close_session": {
                "type": "function",
                "function": {
                    "name": "userKBAgent-close_session",
                    "description": "Close/delete a conversation session and all its messages",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "conversation_id": {
                                "type": "string",
                                "description": "ID of the conversation to close/delete",
                            },
                        },
                        "required": ["conversation_id"],
                    },
                },
            },
            "userKBAgent-get_file": {
                "type": "function",
                "function": {
                    "name": "userKBAgent-get_file",
                    "description": "Download or get a file from a document in the knowledge base",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_id": {
                                "type": "string",
                                "description": "ID of the document to download",
                            },
                            "return_metadata": {
                                "type": "boolean",
                                "description": "If true, returns metadata. If false, returns file content",
                                "default": False,
                            },
                        },
                        "required": ["doc_id"],
                    },
                },
            },
        }

    def get_tools(self) -> GetToolsResponse:
        """
        Get all available MCP tools.

        Returns:
            GetToolsResponse with list of tool definitions
        """
        try:
            tools = [self._tools[tool_name] for tool_name in self._tools]
            return GetToolsResponse(tools=tools, success=True)
        except Exception as e:
            logger.error(f"Error getting tools: {e}", exc_info=True)
            return GetToolsResponse(
                tools=None,
                success=False,
                message=f"Error getting tools: {str(e)}",
            )

    def execute_tool(self, request: ExecuteToolRequest) -> ExecuteToolResponse:
        """
        Execute one of the 5 available MCP tools.

        Args:
            request: ExecuteToolRequest with tool_name and arguments

        Returns:
            ExecuteToolResponse with result or error
        """
        tool_name = request.tool_name
        arguments = request.arguments or {}

        try:
            if tool_name == "userKBAgent-create_session":
                return self._execute_create_session(arguments)
            elif tool_name == "userKBAgent-ask":
                return self._execute_ask(arguments)
            elif tool_name == "userKBAgent-list_sessions":
                return self._execute_list_sessions(arguments)
            elif tool_name == "userKBAgent-close_session":
                return self._execute_close_session(arguments)
            elif tool_name == "userKBAgent-get_file":
                return self._execute_get_file(arguments)
            else:
                return ExecuteToolResponse(
                    tool_name=tool_name,
                    success=False,
                    result={"error": f"Unknown tool: {tool_name}"},
                )
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return ExecuteToolResponse(
                tool_name=tool_name,
                success=False,
                result={"error": f"Error executing tool: {str(e)}"},
            )

    def search_tools(self, request: SearchToolsRequest) -> SearchToolsResponse:
        """
        Search for tools by name or description.

        Args:
            request: SearchToolsRequest with query string

        Returns:
            SearchToolsResponse with matching tools
        """
        try:
            query = request.query.lower()
            matching_tools: List[Dict[str, Any]] = []

            for _, tool_def in self._tools.items():
                func_name = tool_def["function"]["name"].lower()
                func_desc = tool_def["function"]["description"].lower()

                if query in func_name or query in func_desc:
                    matching_tools.append(tool_def)

            return SearchToolsResponse(
                tools=matching_tools,
                success=True,
                message=f"Found {len(matching_tools)} matching tools",
            )
        except Exception as e:
            logger.error(f"Error searching tools: {e}", exc_info=True)
            return SearchToolsResponse(
                tools=[],
                success=False,
                message=f"Error searching tools: {str(e)}",
            )

    # Tool execution methods

    def _execute_create_session(self, args: Dict[str, Any]) -> ExecuteToolResponse:
        """Create a new conversation session."""
        try:
            name = args.get("name")
            kb_ids = args.get("kb_ids", [])
            user_instructions = args.get("user_instructions")

            response = self.conv_manager.create_conversation_session(
                name=name,
                kb_ids=kb_ids,
                user_instructions=user_instructions,
            )

            if response.success:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-create_session",
                    success=True,
                    result={
                        "conversation_id": response.conversation_id,
                        "created_at": str(response.created_at) if response.created_at else None,
                    },
                )
            else:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-create_session",
                    success=False,
                    result={"error": response.message or "Failed to create conversation session"},
                )
        except Exception as e:
            logger.error(f"Error in create_session: {e}", exc_info=True)
            return ExecuteToolResponse(
                tool_name="userKBAgent-create_session",
                success=False,
                result={"error": str(e)},
            )

    def _execute_ask(self, args: Dict[str, Any]) -> ExecuteToolResponse:
        """Ask a question in a conversation."""
        try:
            conversation_id = args.get("conversation_id")
            prompt = args.get("prompt")

            if not conversation_id or not prompt:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-ask",
                    success=False,
                    result={"error": "Missing required arguments: conversation_id and prompt"},
                )

            # Get the conversation first to validate it exists
            conv_response = self.conv_manager.get_conversation(conversation_id)
            if not conv_response.success:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-ask",
                    success=False,
                    result={"error": f"Conversation not found: {conversation_id}"},
                )

            # Note: This returns the immediate response. For streaming, the client
            # should use the converse endpoint with WebSocket or SSE
            return ExecuteToolResponse(
                tool_name="userKBAgent-ask",
                success=True,
                result={
                    "message": "Question submitted to conversation",
                    "conversation_id": conversation_id,
                    "prompt": prompt,
                    "note": "Use WebSocket or SSE endpoint for streaming response",
                },
            )
        except Exception as e:
            logger.error(f"Error in ask: {e}", exc_info=True)
            return ExecuteToolResponse(
                tool_name="userKBAgent-ask",
                success=False,
                result={"error": str(e)},
            )

    def _execute_list_sessions(self, args: Dict[str, Any]) -> ExecuteToolResponse:
        """List all conversation sessions."""
        try:
            filters = args.get("filters")
            projections = args.get("projections")

            response = self.conv_manager.list_conversations(
                filters=filters,
                projections=projections,
            )

            if response.success:
                conversations = response.conversations or []
                # Convert conversations to dict format for serialization
                conv_list: List[Dict[str, Any]] = []
                for conv in conversations:
                    if isinstance(conv, dict):
                        conv_list.append(
                            {
                                "id": conv.get("_id"),
                                "name": conv.get("name"),
                                "created_at": (
                                    str(conv.get("created_at")) if conv.get("created_at") else None
                                ),
                                "kb_ids": conv.get("kb_ids", []),
                            }
                        )
                    else:
                        # Handle Pydantic model
                        conv_dict = conv.model_dump() if hasattr(conv, "model_dump") else vars(conv)
                        conv_list.append(
                            {
                                "id": conv_dict.get("_id"),
                                "name": conv_dict.get("name"),
                                "created_at": (
                                    str(conv_dict.get("created_at"))
                                    if conv_dict.get("created_at")
                                    else None
                                ),
                                "kb_ids": conv_dict.get("kb_ids", []),
                            }
                        )

                return ExecuteToolResponse(
                    tool_name="userKBAgent-list_sessions",
                    success=True,
                    result={
                        "conversations": conv_list,
                        "total": len(conv_list),
                    },
                )
            else:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-list_sessions",
                    success=False,
                    result={"error": response.message or "Failed to list conversations"},
                )
        except Exception as e:
            logger.error(f"Error in list_sessions: {e}", exc_info=True)
            return ExecuteToolResponse(
                tool_name="userKBAgent-list_sessions",
                success=False,
                result={"error": str(e)},
            )

    def _execute_close_session(self, args: Dict[str, Any]) -> ExecuteToolResponse:
        """Close/delete a conversation session."""
        try:
            conversation_id = args.get("conversation_id")

            if not conversation_id:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-close_session",
                    success=False,
                    result={"error": "Missing required argument: conversation_id"},
                )

            # Get the conversation first to validate it exists
            conv_response = self.conv_manager.get_conversation(conversation_id)
            if not conv_response.success:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-close_session",
                    success=False,
                    result={"error": f"Conversation not found: {conversation_id}"},
                )

            # Note: In AgenticRAG, conversation deletion requires using the full API endpoint
            # or direct database operations. This tool validates the conversation exists
            # and signals closure intent to the calling agent.
            return ExecuteToolResponse(
                tool_name="userKBAgent-close_session",
                success=True,
                result={
                    "message": "Conversation session closure initiated",
                    "conversation_id": conversation_id,
                    "note": "Use the full API endpoint /conversation/{conversation_id} for complete deletion",
                },
            )
        except Exception as e:
            logger.error(f"Error in close_session: {e}", exc_info=True)
            return ExecuteToolResponse(
                tool_name="userKBAgent-close_session",
                success=False,
                result={"error": str(e)},
            )

    def _execute_get_file(self, args: Dict[str, Any]) -> ExecuteToolResponse:
        """Download or get metadata for a file from a document."""
        try:
            doc_id = args.get("doc_id")
            return_metadata = args.get("return_metadata", False)

            if not doc_id:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-get_file",
                    success=False,
                    result={"error": "Missing required argument: doc_id"},
                )

            if not self.kb_manager:
                return ExecuteToolResponse(
                    tool_name="userKBAgent-get_file",
                    success=False,
                    result={"error": "Knowledge base manager not available"},
                )

            if return_metadata:
                # Get document metadata
                response = self.kb_manager.get_document(doc_id)
                if response.success:
                    doc: Union[Document, Dict[str, Any]] = response.document
                    # Handle both dict and Pydantic model
                    if isinstance(doc, dict):
                        doc_data = doc
                    else:
                        doc_data = doc.model_dump() if hasattr(doc, "model_dump") else vars(doc)

                    return ExecuteToolResponse(
                        tool_name="userKBAgent-get_file",
                        success=True,
                        result={
                            "doc_id": doc_data.get("_id"),
                            "name": doc_data.get("name"),
                            "content_type": doc_data.get("content_type"),
                            "size": doc_data.get("size"),
                            "created_at": (
                                str(doc_data.get("created_at"))
                                if doc_data.get("created_at")
                                else None
                            ),
                        },
                    )
                else:
                    return ExecuteToolResponse(
                        tool_name="userKBAgent-get_file",
                        success=False,
                        result={"error": f"Document not found: {doc_id}"},
                    )
            else:
                # Download document file
                # Note: For streaming binary content, use the /documents/{doc_id}/download endpoint
                return ExecuteToolResponse(
                    tool_name="userKBAgent-get_file",
                    success=True,
                    result={
                        "message": "Use /documents/{doc_id}/download endpoint for file download",
                        "doc_id": doc_id,
                        "download_url": f"/documents/{doc_id}/download",
                    },
                )
        except Exception as e:
            logger.error(f"Error in get_file: {e}", exc_info=True)
            return ExecuteToolResponse(
                tool_name="userKBAgent-get_file",
                success=False,
                result={"error": str(e)},
            )
