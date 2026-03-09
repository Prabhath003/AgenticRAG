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

# api/authentication.py
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.exceptions import HTTPException, WebSocketException
from fastapi import Depends, Header, WebSocket, status
from typing import Optional
from fastapi.security import HTTPBearer
import hashlib
from enum import StrEnum
from pydantic import Field
from datetime import datetime, timezone
from uuid import uuid4

from src.config import Config
from src.infrastructure.database import get_db_session
from src.core.models.core_models import DatabaseBaseModel

security = HTTPBearer(auto_error=False)


def _hash_api_key(api_key: str) -> str:
    """Hash API key using PBKDF2 (NIST-approved key derivation function).

    Args:
        api_key: The raw API key to hash

    Returns:
        Hex-encoded hash suitable for secure storage
    """
    # PBKDF2 with SHA-256, 480000 iterations (NIST recommendation as of 2023)
    hash_obj = hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        b"agentic-rag-api-key",  # Salt: fixed but can be changed
        480000,  # Iterations (high computational cost for brute-force resistance)
    )
    return hash_obj.hex()


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


class User(DatabaseBaseModel):
    user_id: str = Field(default_factory=lambda: str(uuid4()), alias="_id")  # type: ignore[override]
    name: Optional[str] = Field(default=None, description="User's full name")
    email: Optional[str] = Field(default=None, description="User's email address")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when user was created",
    )
    updated_at: Optional[datetime] = Field(
        default=None, description="Timestamp when user was last updated"
    )


class APIKey(DatabaseBaseModel):
    masked_api_key: str
    api_hash: Optional[str] = Field(default=None)
    role: Role = Field(default=Role.USER)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_api_key(cls, user_id: str, api_key: str, role: Role = Role.USER) -> "APIKey":
        """Create an APIKey instance from a raw API key.

        Args:
            api_key: The raw API key string
            role: The role for this API key (default: USER)

        Returns:
            APIKey instance with masked_api_key and api_hash populated
        """
        # Create masked version (show first 8 and last 4 characters)
        masked = f"{api_key[:8]}{'*' * max(0, len(api_key) - 12)}{api_key[-4:]}"
        # Hash using PBKDF2 (NIST-approved key derivation function)
        api_hash = _hash_api_key(api_key)
        return cls(masked_api_key=masked, user_id=user_id, api_hash=api_hash, role=role)


async def verify_api_key(
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    Verify API key from Authorization header
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    api_key = authorization.credentials
    api_hash = _hash_api_key(api_key)

    # use connection pool
    with get_db_session() as db:
        coll = db[Config.API_KEYS_COLLECTION]
        key_doc = coll.find_one({"api_hash": api_hash})

    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key


# Alternative function for X-API-Key header (if you prefer this format)
async def verify_api_key_header(x_api_key: Optional[str] = Header(None)):
    """Verify API key from X-API-Key Header and return user_id"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")

    api_hash = _hash_api_key(x_api_key)

    # Check if API key exists in database:
    with get_db_session() as db:
        coll = db[Config.API_KEYS_COLLECTION]
        key_doc = coll.find_one({"api_hash": api_hash})

    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return key_doc.get("user_id")


def verify_api_key_direct(api_key: str) -> bool:
    """
    Verify API key directly without FastAPI dependency injection.
    Returns True if valid, raises HTTPException if invalid.
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")

    api_hash = _hash_api_key(api_key)

    with get_db_session() as db:
        coll = db[Config.API_KEYS_COLLECTION]
        key_doc = coll.find_one({"api_hash": api_hash})

    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


async def verify_admin_api_key(x_api_key: Optional[str] = Header(None)):
    """
    Verify API key from X-API-Key Header and check for ADMIN role.
    Only admin role API keys can access protected endpoints.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")

    api_hash = _hash_api_key(x_api_key)

    # Check if API key exists and has admin role
    with get_db_session() as db:
        coll = db[Config.API_KEYS_COLLECTION]
        key_doc = coll.find_one({"api_hash": api_hash})

    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check if the API key has admin role
    if key_doc.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required for this operation")

    return api_hash


async def validate_websocket_api_key(websocket: WebSocket) -> str:
    """
    Validate API key from WebSocket headers.

    Raises WebSocketException with POLICY_VIOLATION if validation fails.
    Returns the validated user_id.
    """
    api_key = websocket.headers.get("x-api-key")

    if not api_key:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    try:
        # Use the same validation logic as verify_api_key_header
        api_hash = _hash_api_key(api_key)

        # Check if API key exists in database
        with get_db_session() as db:
            coll = db[Config.API_KEYS_COLLECTION]
            key_doc = coll.find_one({"api_hash": api_hash})

        if not key_doc:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

        # Return the user_id associated with this API key
        user_id = key_doc.get("user_id")
        if not user_id:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

        return user_id
    except WebSocketException:
        raise
    except Exception:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
