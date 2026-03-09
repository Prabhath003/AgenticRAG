from typing import Dict, Any, List, Optional

from ._tools import BaseTool
from ._tools.workspace import BashTool, ReadFileTool, ShowFilesTool
from ._tools.text_editor import (
    CreateFileTool,
    EditFileTool,
    FileLineInsertTool,
    UndoFileEditTool,
    ViewTool,
)
from ._tools.user_kbs import (
    UserKBsIndex,
    ListAllKBsTool,
    ListKBDocumentsTool,
    ListDocumentChunksTool,
    GetChunkTool,
    SemanticSearchTool,
    GetChunkContextTool,
    GetPreviousChunkTool,
    GetNextChunkTool,
)
from ...models.agent import (
    Settings,
    Style,
    ConverseFile,
    ConversationFileMetadata,
)
from .._conversation_manager import ConversationManager
from ...models.agent.content_models import ToolResultContent
from ....infrastructure.operation_logging import get_operation_id, get_operation_user_id


class ToolManager:
    """
    Tool manager that manages and executes all available tools.

    Tools are defined as a class list. Manager automatically:
    - Initializes all tool instances
    - Provides get_tools() for OpenAI function calling
    - Executes tools by name with kwargs
    """

    # List of tool classes to register
    TOOL_CLASSES: List[type[BaseTool]] = [
        BashTool,
        ReadFileTool,
        CreateFileTool,
        EditFileTool,
        ShowFilesTool,
        FileLineInsertTool,
        UndoFileEditTool,
        ViewTool,
        ListAllKBsTool,
        ListKBDocumentsTool,
        ListDocumentChunksTool,
        GetChunkTool,
        SemanticSearchTool,
        GetChunkContextTool,
        GetPreviousChunkTool,
        GetNextChunkTool,
    ]

    def __init__(self, conversation_manager: "ConversationManager"):
        self.conversation_manager = conversation_manager

        self.user_kbs = UserKBsIndex(self.conversation_manager.kb_ids, get_operation_user_id())

        # Initialize tools from TOOL_CLASSES
        self._tools: Dict[str, BaseTool] = {}
        for tool_class in self.TOOL_CLASSES:
            # Pass conversation_id to workspace and text editor tools
            if tool_class.__name__ in (
                "BashTool",
                "ReadFileTool",
                "CreateFileTool",
                "EditFileTool",
                "ShowFilesTool",
                "FileLineInsertTool",
                "UndoFileEditTool",
                "ViewTool",
            ):
                tool_instance = tool_class(conversation_id=conversation_manager.conversation_id)  # type: ignore
            # Pass shared UserKBsIndex to user_kbs tools
            elif tool_class.__name__ in (
                "ListAllKBsTool",
                "ListKBDocumentsTool",
                "ListDocumentChunksTool",
                "GetChunkTool",
                "SemanticSearchTool",
                "GetChunkContextTool",
                "GetPreviousChunkTool",
                "GetNextChunkTool",
            ):
                tool_instance = tool_class(index=self.user_kbs)  # type: ignore
            else:
                tool_instance = tool_class()
            self._tools[tool_instance.name] = tool_instance

        # Cache frequently-accessed tools
        self._bash_tool: Optional[BashTool] = self._tools.get("bash_tool")  # type: ignore
        self._edit_file_tool: Optional[EditFileTool] = self._tools.get("edit_file")  # type: ignore

    def upload(self, files: List[ConverseFile]) -> List[ConversationFileMetadata]:
        if not self._bash_tool:
            raise RuntimeError("BashTool not initialized")
        return self._bash_tool.upload(files)
        # Alternative approach using execute_function:
        # return self.execute_function("upload", "bash_tool", files=files)

    def get_files(self):
        if not self._bash_tool:
            raise RuntimeError("BashTool not initialized")
        return self._bash_tool.get_files()
        # Alternative approach using execute_function:
        # return self.execute_function("get_files", "bash_tool")

    def download_content(self, file_path: str, version: Optional[int] = None):
        if not self._bash_tool:
            raise RuntimeError("BashTool not initialized")
        return self._bash_tool.download_content(file_path, version=version)
        # Alternative approach using execute_function:
        # return self.execute_function("download_content", "bash_tool", file_path=file_path)

    def edit_file(self, file_path: str, new_content: str):
        if not self._edit_file_tool:
            raise RuntimeError("EditTool not initialized")

        # Get the old file content as tuple (bytes, filename, content_type)
        file_content_tuple = self.download_content(file_path)

        # Extract bytes and convert to string
        file_content_bytes, _, _ = file_content_tuple
        old_content = file_content_bytes.decode("utf-8")

        # Generate a unique func_call_id
        func_call_id = f"edit_file_{hash(file_path + old_content)}"

        # Create replacement operation for string mode
        replacements = [{"type": "string", "old_str": old_content, "new_str": new_content}]

        # Call the edit file tool with required parameters
        return self._edit_file_tool.execute(
            func_call_id=get_operation_id() or func_call_id,
            path=file_path,
            replacements=replacements,
            show=True,
        )

    def get_tools(
        self,
        settings: Optional[Settings] = None,
        personalized_styles: Optional[List[Style]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get available tools in OpenAI function format, filtered by settings.

        Args:
            settings: Settings object with enabled_* flags (e.g.)
            personalized_styles: List of Style objects (for future use in tool customization)

        Returns:
            List of tool definitions in OpenAI format
        """
        # Default settings - all features enabled
        if settings is None:
            settings = Settings()

        # Tool enable/disable mapping
        tool_setting_map: Dict[str, str] = {}

        tools: List[Dict[str, Any]] = []
        for tool_name, tool in self._tools.items():
            # Check if this tool has a setting that controls it
            setting_key = tool_setting_map.get(tool_name)

            if setting_key:
                # Only include if the setting is enabled
                if getattr(settings, setting_key, True):
                    tools.append(tool.get_tool_info())
            else:
                # Tools without explicit settings are always included (bash, file ops, expert tools)
                tools.append(tool.get_tool_info())

        return tools

    def execute_function(
        self, func_call_id: str, function_name: str, **kwargs: Any
    ) -> "ToolResultContent":
        """
        Execute a tool by name with keyword arguments.

        Args:
            function_name: Name of the function/tool to execute
            **kwargs: Parameters to pass to the tool

        Returns:
            ToolResultContent from the tool execution
        """

        if function_name not in self._tools:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=function_name,
                message=f"Error: Unknown tool '{function_name}'. Available tools: {list(self._tools.keys())}",
                is_error=True,
            )

        try:
            tool = self._tools[function_name]
            return tool.execute(func_call_id, **kwargs)
        except Exception as e:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=function_name,
                message=f"Error executing {function_name}: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, function_name: str, **kwargs: Any) -> "Any":
        """
        Get display content for a tool execution.

        Args:
            function_name: Name of the function/tool
            **kwargs: Parameters to pass to the tool

        Returns:
            DisplayContent object or None if not implemented
        """
        if function_name not in self._tools:
            return None

        try:
            tool = self._tools[function_name]
            return tool.get_display_content(**kwargs)
        except Exception:
            return None

    async def compact(self, message_id: str):
        await self.conversation_manager.compact_history(message_id)
