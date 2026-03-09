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

"""Pydantic models for FastAPI endpoints"""

# api/models.py
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

from src.core.models.response_models import BaseResponse

from ._authentication import APIKey, Role, User


class GenerateAPIKeyResponse(BaseResponse):
    api_key: Optional[str] = Field(default=None)


class ListAPIKeysResponse(BaseResponse):
    api_keys: List[APIKey] = Field(default_factory=lambda: [])


class DeleteAPIKeysResponse(BaseResponse):
    api_keys_deleted: List[str] = Field(default_factory=lambda: [])


class GenerateAPIKeyRequest(BaseModel):
    """Request model for generating API keys"""

    user_id: str
    role: Role = Field(
        default=Role.USER,
        title="Role",
        description="Role for the new key - 'user' (default) or 'admin'",
    )


class ListAPIKeysRequest(BaseModel):
    """Request model for listing API keys"""

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description="MongoDB-style filters to apply to API keys (e.g., {'role': 'admin'})",
    )
    projections: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Projections",
        description="MongoDB-style projections to specify which fields to include/exclude",
    )


class DeleteAPIKeysRequest(BaseModel):
    """Request model for deleting API keys"""

    api_keys: List[str] = Field(..., title="API Keys", description="List of API keys to delete")


# ============================================================================
# User Endpoints Models
# ============================================================================


class CreateUserRequest(BaseModel):
    """Request model for creating a user"""

    name: Optional[str] = Field(default=None, title="Name", description="User's full name")
    email: Optional[str] = Field(default=None, title="Email", description="User's email address")


class CreateUserResponse(BaseResponse):
    user: Optional[User] = Field(default=None)


class ListUsersRequest(BaseModel):
    """Request model for listing users"""

    filters: Dict[str, Any] = Field(
        default_factory=dict,
        title="Filters",
        description="MongoDB-style filters to apply to users",
    )
    projections: Dict[str, Any] = Field(
        default_factory=dict,
        title="Projections",
        description="MongoDB-style projections to specify which fields to include/exclude",
    )


class ListUsersResponse(BaseResponse):
    users: List[User] = Field(default_factory=lambda: [])


class UpdateUserRequest(BaseModel):
    """Request model for updating a user"""

    name: Optional[str] = Field(default=None, title="Name", description="User's full name")
    email: Optional[str] = Field(default=None, title="Email", description="User's email address")


class UpdateUserResponse(BaseResponse):
    user: Optional[User] = Field(default=None)


class DeleteUserResponse(BaseResponse):
    user_id: Optional[str] = Field(default=None, description="ID of the deleted user")
    api_keys_deleted: int = Field(default=0, description="Number of API keys deleted with the user")
