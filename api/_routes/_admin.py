"""Admin endpoints for API key and user management."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
import secrets
from typing import List, Dict, Any

from .._authentication import verify_admin_api_key, APIKey, User, _hash_api_key
from .._models import (
    GenerateAPIKeyRequest,
    ListAPIKeysRequest,
    DeleteAPIKeysRequest,
    GenerateAPIKeyResponse,
    ListAPIKeysResponse,
    DeleteAPIKeysResponse,
    CreateUserRequest,
    CreateUserResponse,
    ListUsersRequest,
    ListUsersResponse,
    UpdateUserRequest,
    UpdateUserResponse,
    DeleteUserResponse,
)
from src.config import Config
from src.infrastructure.database import get_db_session

router = APIRouter(prefix="/admin")


# ============================================================================
# API Key Endpoints
# ============================================================================


@router.post("/api_keys/generate", tags=["api_key"])
async def generate_api_keys(
    request: GenerateAPIKeyRequest, api_hash: str = Depends(verify_admin_api_key)
):
    """Generate a new API key. Only admin role keys can access this endpoint.

    Request body:
        user_id: User ID for which to generate the key
        role: Role for the new key - "user" (default) or "admin"
    """
    with get_db_session() as db:
        # Verify that user_id exists in USERS collection
        users_coll = db[Config.USERS_COLLECTION]
        user = users_coll.find_one({"_id": request.user_id})

        if not user:
            return JSONResponse(
                status_code=400,
                content=GenerateAPIKeyResponse(
                    success=False, message=f"User with ID '{request.user_id}' not found"
                ).model_dump(mode="json", exclude_none=True),
            )

        # Generate a secure 32-character API key
        api_keys_coll = db[Config.API_KEYS_COLLECTION]
        key = "titli-" + secrets.token_urlsafe(32)
        api_key_obj = APIKey.from_api_key(user_id=request.user_id, api_key=key, role=request.role)
        entry = api_key_obj.model_dump(by_alias=True, exclude_none=True)

        api_keys_coll.insert_one(entry)
    response = GenerateAPIKeyResponse(message="API key generated successfully", api_key=key)
    return response


@router.post("/api_keys/list", tags=["api_key"])
async def get_api_keys(request: ListAPIKeysRequest, api_hash: str = Depends(verify_admin_api_key)):
    """List API keys with optional filtering and projections. Only accessible by admin role keys.

    Request body:
        filters: MongoDB-style filters to apply
        projections: MongoDB-style projections to specify which fields to include/exclude
    """
    # Set default projections if not provided
    projections = request.projections or {}
    filters = request.filters or {}

    with get_db_session() as db:
        if projections:
            keys = list(db[Config.API_KEYS_COLLECTION].find(filters, projections))
        else:
            keys = list(db[Config.API_KEYS_COLLECTION].find(filters))

    api_keys_list = [APIKey(**k) for k in keys]
    response = ListAPIKeysResponse(
        message="API keys retrieved successfully", api_keys=api_keys_list
    )
    return response


@router.post("/api_keys/delete", tags=["api_key"])
async def delete_api_keys(
    request: DeleteAPIKeysRequest, admin_api_hash: str = Depends(verify_admin_api_key)
):
    """Delete API keys by their values. Only accessible by admin role keys.

    Request body:
        api_keys: List of API keys to delete
    """
    deleted_keys: List[str] = []
    with get_db_session() as db:
        for api_key in request.api_keys:
            api_hash = _hash_api_key(api_key)
            result = db[Config.API_KEYS_COLLECTION].delete_one({"api_hash": api_hash})
            if result.deleted_count > 0:
                deleted_keys.append(api_key)

    response = DeleteAPIKeysResponse(
        message=f"Deleted {len(deleted_keys)} API key(s)", api_keys_deleted=deleted_keys
    )
    return response


# ============================================================================
# User Endpoints
# ============================================================================


@router.post("/users/create", tags=["users"])
async def create_user(request: CreateUserRequest, user_id: str = Depends(verify_admin_api_key)):
    """Create a new user. Only admin role keys can access this endpoint.

    Request body:
        name: User's full name (optional)
        email: User's email address (optional)
    """
    with get_db_session() as db:
        users_coll = db[Config.USERS_COLLECTION]

        # Create a new user instance
        user = User(name=request.name, email=request.email)
        entry = user.model_dump(by_alias=True, exclude_none=True)

        users_coll.insert_one(entry)

    response = CreateUserResponse(message="User created successfully", user=user)
    return response


@router.post("/users/list", tags=["users"])
async def list_users(request: ListUsersRequest, user_id: str = Depends(verify_admin_api_key)):
    """List users with optional filtering and projections. Only accessible by admin role keys.

    Request body:
        filters: MongoDB-style filters to apply
        projections: MongoDB-style projections to specify which fields to include/exclude
    """
    # Set default projections if not provided
    projections = request.projections or {
        "_id": 1,
        "name": 1,
        "email": 1,
        "created_at": 1,
    }
    filters = request.filters or {}

    with get_db_session() as db:
        if projections:
            users = list(db[Config.USERS_COLLECTION].find(filters, projections))
        else:
            users = list(db[Config.USERS_COLLECTION].find(filters))

    users_list = [User(**u) for u in users]
    response = ListUsersResponse(message="Users retrieved successfully", users=users_list)
    return response


@router.put("/users/{user_id}", tags=["users"])
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    _user_id: str = Depends(verify_admin_api_key),
):
    """Update a user. Only accessible by admin role keys.

    Path Args:
        user_id: User ID to update

    Request body:
        name: User's full name (optional)
        email: User's email address (optional)
    """
    with get_db_session() as db:
        users_coll = db[Config.USERS_COLLECTION]

        # Check if user exists
        user = users_coll.find_one({"_id": user_id})
        if not user:
            return JSONResponse(
                status_code=400,
                content=UpdateUserResponse(
                    success=False, message=f"User with ID '{user_id}' not found"
                ).model_dump(mode="json", exclude_none=True),
            )

        # Build update document
        update_fields: Dict[str, Any] = {}
        if request.name is not None:
            update_fields["name"] = request.name
        if request.email is not None:
            update_fields["email"] = request.email

        if update_fields:
            update_fields["updated_at"] = datetime.now(timezone.utc)
            users_coll.update_one({"_id": user_id}, {"$set": update_fields})
            user.update(update_fields)

        updated_user = User(**user)
        response = UpdateUserResponse(message="User updated successfully", user=updated_user)
    return response


@router.delete("/users/{user_id}", tags=["users"])
async def delete_user(user_id: str, _user_id: str = Depends(verify_admin_api_key)):
    """Delete a user and cascade delete all associated API keys. Only accessible by admin role keys.

    Path Args:
        user_id: User ID to delete
    """
    with get_db_session() as db:
        users_coll = db[Config.USERS_COLLECTION]
        api_keys_coll = db[Config.API_KEYS_COLLECTION]

        # Check if user exists
        user = users_coll.find_one({"_id": user_id})
        if not user:
            return JSONResponse(
                status_code=400,
                content=DeleteUserResponse(
                    success=False, message=f"User with ID '{user_id}' not found"
                ).model_dump(mode="json", exclude_none=True),
            )

        # Delete all API keys for this user
        api_keys_result = api_keys_coll.delete_many({"user_id": user_id})

        # Delete the user
        users_coll.delete_one({"_id": user_id})

    response = DeleteUserResponse(
        message=f"User deleted successfully along with {api_keys_result.deleted_count} API key(s)",
        user_id=user_id,
        api_keys_deleted=api_keys_result.deleted_count,
    )
    return response
