from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .._authentication import verify_api_key_header
from .._dependencies import task_manager
from src.core.models._request_models import (
    ExecuteToolRequest,
    SearchToolsRequest,
)
from src.core.models.operation_audit import OperationType
from src.infrastructure.operation_logging import operation_endpoint
from src.log_creator import get_file_logger

logger = get_file_logger()
router = APIRouter(prefix="/mcp-server", tags=["MCP-Server"])


@router.get("/get-tools", tags=["MCP-Server"])
@operation_endpoint(OperationType.GET_TOOLS)
async def get_tools(user_id: str = Depends(verify_api_key_header)):
    try:
        response = task_manager.mcp_server.get_tools()
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error listing tools: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/execute-tool", tags=["MCP-Server"])
@operation_endpoint(OperationType.EXECUTE_TOOL)
async def execute_tool(request: ExecuteToolRequest, user_id: str = Depends(verify_api_key_header)):
    try:
        response = task_manager.mcp_server.execute_tool(request)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error executing tool: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/search-tools", tags=["MCP-Server"])
@operation_endpoint(OperationType.SEARCH_TOOLS)
async def search_tools(request: SearchToolsRequest, user_id: str = Depends(verify_api_key_header)):
    try:
        response = task_manager.mcp_server.search_tools(request)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error searching tools: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
