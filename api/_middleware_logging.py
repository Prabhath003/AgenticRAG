# api/middleware_logging.py
import time
import json
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from datetime import datetime, timezone
from typing import Callable, Any, Dict, Optional

from src.log_creator import get_file_logger

logger = get_file_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all HTTP requests and responses"""

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        # Record start time
        start_time = time.time()

        # Extract request info
        request_id = request.headers.get("X-Request-ID", f"req-{int(start_time * 1000)}")
        method = request.method
        path = request.url.path
        query_string = request.url.query
        client_host = request.client.host if request.client else "unknown"

        # Log request
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "event": "request_received",
            "method": method,
            "path": path,
            "query": query_string or None,
            "client": client_host,
            "headers": {
                "user_agent": request.headers.get("user-agent", "unknown"),
                "accept": request.headers.get("accept", "unknown"),
                "content_type": request.headers.get("content-type", "unknown"),
            },
        }

        # Check for API key in headers (log presence, not value)
        has_api_key = "x-api-key" in request.headers or "authorization" in request.headers
        log_entry["authenticated"] = has_api_key

        logger.info(json.dumps(log_entry))

        try:
            # Process request
            response = await call_next(request)

            # Record response time
            process_time = time.time() - start_time

            # Log response
            response_log: Dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id,
                "event": "request_completed",
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "process_time_ms": round(process_time * 1000, 2),
                "response_headers": {
                    "content_type": response.headers.get("content-type", "unknown"),
                },
            }

            # Determine if successful
            if response.status_code < 400:
                response_log["status"] = "success"
            elif response.status_code < 500:
                response_log["status"] = "client_error"
            else:
                response_log["status"] = "server_error"

            logger.info(json.dumps(response_log))
            return response

        except Exception as e:
            # Log exceptions
            process_time = time.time() - start_time
            error_log: Dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id,
                "event": "request_failed",
                "method": method,
                "path": path,
                "error": str(e),
                "error_type": type(e).__name__,
                "process_time_ms": round(process_time * 1000, 2),
            }
            logger.error(json.dumps(error_log), exc_info=True)
            raise


class AuthenticationLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to specifically log authentication attempts and failures"""

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        # Check authentication headers
        has_api_key = "x-api-key" in request.headers
        has_auth = "authorization" in request.headers

        # Log all auth attempts
        if has_api_key or has_auth:
            auth_log = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "auth_attempt",
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
                "auth_method": "x-api-key" if has_api_key else "bearer_token",
            }
            logger.info(json.dumps(auth_log))

        # Process request
        response = await call_next(request)

        # Log auth failures (401 responses)
        if response.status_code == 401:
            failure_log = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "auth_failed",
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
                "reason": "invalid_credentials",
            }
            logger.warning(json.dumps(failure_log))

        return response


class CORSLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log CORS-related requests and rejections"""

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        # Check for CORS preflight requests
        if request.method == "OPTIONS":
            origin = request.headers.get("origin", "unknown")
            cors_log: Dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "cors_preflight",
                "origin": origin,
                "path": request.url.path,
                "requested_method": request.headers.get("access-control-request-method"),
                "requested_headers": request.headers.get("access-control-request-headers"),
            }
            logger.info(json.dumps(cors_log))

        response = await call_next(request)

        # Log CORS headers in response
        if request.method == "OPTIONS" or "origin" in request.headers:
            cors_response_log: Dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "cors_response",
                "origin": request.headers.get("origin", "unknown"),
                "path": request.url.path,
                "status_code": response.status_code,
                "allowed_origin": response.headers.get("access-control-allow-origin"),
                "allowed_methods": response.headers.get("access-control-allow-methods"),
                "allowed_headers": response.headers.get("access-control-allow-headers"),
            }
            logger.info(json.dumps(cors_response_log))

        return response


class BlockingEventLogger:
    """Utility class to log blocking events from within handlers"""

    @staticmethod
    def log_api_blocked(
        reason: str,
        client: str,
        endpoint: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Log when an API request is blocked"""
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "api_blocked",
            "reason": reason,
            "client": client,
            "endpoint": endpoint,
            "details": details or {},
        }
        logger.warning(json.dumps(log_entry))

    @staticmethod
    def log_rate_limit_exceeded(client: str, endpoint: str):
        """Log rate limit violations"""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "rate_limit_exceeded",
            "client": client,
            "endpoint": endpoint,
        }
        logger.warning(json.dumps(log_entry))

    @staticmethod
    def log_validation_error(endpoint: str, field: str, error: str, client: str):
        """Log validation errors"""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "validation_error",
            "endpoint": endpoint,
            "field": field,
            "error": error,
            "client": client,
        }
        logger.warning(json.dumps(log_entry))
