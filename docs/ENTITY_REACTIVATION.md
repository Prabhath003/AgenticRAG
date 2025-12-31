# Entity Reactivation - Smart Entity Creation

## Overview

When you delete an entity, it's marked as `inactive` but its data is preserved. The improved entity creation system now allows you to **reuse deleted entity IDs** without conflicts.

## How It Works

When creating an entity with `POST /api/entities`:

```
POST /api/entities
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries",
  "description": "..."
}
```

The system performs a three-step check:

### Step 1: Check if Active Entity Exists
```
Does entity_id exist AND status == "active"?
├─ YES → Return 409 Conflict (already exists)
└─ NO → Continue to Step 2
```

### Step 2: Check if Inactive Entity Exists
```
Does entity_id exist AND status == "inactive"?
├─ YES → REACTIVATE it (restore to active)
└─ NO → Continue to Step 3
```

### Step 3: Create New Entity
```
No entity found with that ID?
└─ CREATE new entity
```

## Scenarios

### Scenario 1: Creating New Entity (First Time)

```bash
# Create entity for the first time
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "entity_name": "TechCorp Industries"
  }'

# Response: 200 OK
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries",
  "created_at": "2025-01-15T10:30:00Z",
  "total_documents": 0,
  "total_chunks": 0
}
```

### Scenario 2: Creating With Existing Active Entity (Error)

```bash
# Try to create with same entity_id
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "entity_name": "TechCorp Updated"
  }'

# Response: 409 Conflict
{
  "error": "Entity company_123 already exists"
}
```

### Scenario 3: Reactivating Deleted Entity (Success)

```bash
# 1. Delete entity
curl -X DELETE http://localhost:8000/api/entities/company_123

# 2. Try to create with same entity_id
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "entity_name": "TechCorp Industries",
    "description": "Recreated entity"
  }'

# Response: 200 OK - REACTIVATED
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries",
  "description": "Recreated entity",
  "created_at": "2025-01-15T10:30:00Z",  # Original creation time
  "total_documents": 5,                   # Previous documents still there!
  "total_chunks": 127
}
```

## API Responses

### 200 OK - Successfully Created or Reactivated
```json
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries",
  "description": "Optional description",
  "metadata": {},
  "created_at": "2025-01-15T10:30:00Z",
  "total_documents": 0,
  "total_chunks": 0,
  "has_vector_store": false
}
```

### 409 Conflict - Entity Already Active
```json
{
  "error": "Entity company_123 already exists"
}
```

## Behavior Details

### When Reactivating

When you reactivate a deleted entity, the system:

1. **Preserves Original Data**
   - `created_at`: Original creation timestamp
   - `documents`: All previously uploaded documents
   - `chunks`: All indexed chunks

2. **Updates Metadata**
   - `entity_name`: Updated to new value
   - `description`: Updated if provided
   - `metadata`: Updated if provided

3. **Tracks Reactivation**
   - `reactivated_at`: Timestamp of reactivation
   - `status`: Changed from "inactive" back to "active"

4. **Preserves History**
   - All request metrics for reactivated entity are now queryable again
   - Cost history becomes accessible again
   - All soft-deleted sessions can be queried again

### Storage State

```
Before Deletion:
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries",
  "status": "active",
  "created_at": "2025-01-15T10:30:00Z",
  "documents": [...]
}

After Deletion:
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries",
  "status": "inactive",  ← Changed to inactive
  "created_at": "2025-01-15T10:30:00Z",
  "deactivated_at": "2025-01-15T11:00:00Z",
  "documents": [...]    ← Preserved
}

After Reactivation:
{
  "entity_id": "company_123",
  "entity_name": "TechCorp Industries Updated",  ← Updated
  "status": "active",  ← Back to active
  "created_at": "2025-01-15T10:30:00Z",  ← Original timestamp
  "deactivated_at": "2025-01-15T11:00:00Z",  ← Deletion timestamp
  "reactivated_at": "2025-01-15T11:30:00Z",  ← Reactivation timestamp
  "documents": [...]  ← Still there!
}
```

## Use Cases

### Use Case 1: Development/Testing

```bash
# Test with company_123
POST /api/entities with entity_id=company_123

# Done with testing, delete it
DELETE /api/entities/company_123

# Later, test again with same ID
POST /api/entities with entity_id=company_123  # ✓ Works! No "already exists" error
```

### Use Case 2: Tenant Churn

```bash
# Tenant onboards
POST /api/entities with entity_id=tenant_456

# Tenant uses system
# ...uploads files, chats, costs accumulate...

# Tenant pauses (deletes entity)
DELETE /api/entities/tenant_456

# Tenant comes back later, reactivates account
POST /api/entities with entity_id=tenant_456
# ✓ All data, documents, history restored!
```

### Use Case 3: Account Recovery

```bash
# User accidentally deletes entity
DELETE /api/entities/company_789

# Realizes mistake immediately
# Can recreate with same ID in seconds
POST /api/entities with entity_id=company_789
# ✓ All documents and cost history back!
```

## Query History After Reactivation

Once an entity is reactivated, all cost tracking becomes queryable again:

```bash
# Entity was deleted, so cost reports returned empty
curl http://localhost:8000/api/cost-report?entity_id=company_123
# Before: {"total_cost_usd": 0.0, "total_requests": 0}

# After reactivation
curl http://localhost:8000/api/cost-report?entity_id=company_123
# After: {"total_cost_usd": 5.50, "total_requests": 15}  # Costs restored!
```

## Helper Functions

Three new functions for entity status management:

```python
# Get entity regardless of status (includes deleted)
get_entity_from_storage(entity_id: str) -> Optional[Dict]
# Returns: Entity if exists (active or inactive), None otherwise

# Get only ACTIVE entity
get_active_entity_from_storage(entity_id: str) -> Optional[Dict]
# Returns: Entity only if status == "active", None otherwise

# Get only INACTIVE (deleted) entity
get_inactive_entity_from_storage(entity_id: str) -> Optional[Dict]
# Returns: Entity only if status == "inactive", None otherwise
```

## Implementation

### Modified Files

**api/main.py**:
- Added `get_active_entity_from_storage()` helper
- Added `get_inactive_entity_from_storage()` helper
- Updated `create_entity()` endpoint to handle reactivation
- Changed status code from 200 to 409 for "already exists" (HTTP standard)

### Logic Flow

```python
async def create_entity(entity: EntityCreate):
    # Check for active entity
    active = get_active_entity_from_storage(entity.entity_id)
    if active:
        return 409  # Conflict

    # Check for inactive (deleted) entity
    inactive = get_inactive_entity_from_storage(entity.entity_id)
    if inactive:
        # Reactivate it
        entity_data = inactive.copy()
        entity_data["status"] = "active"
        entity_data["reactivated_at"] = now()
        # Update fields
        entity_data["entity_name"] = entity.entity_name
        # ... update other fields ...
        save_entity(entity_data)
        return EntityResponse(...)

    # Create new entity
    entity_data = {...}
    save_entity(entity_data)
    return EntityResponse(...)
```

## Benefits

✅ **No More "Already Exists" Errors**: Reuse deleted entity IDs
✅ **Data Preservation**: Documents and history survive deletion
✅ **Transparent Reactivation**: Works like creating new entity from user perspective
✅ **Audit Trail**: `created_at`, `deactivated_at`, `reactivated_at` timestamps
✅ **Cost History Restored**: Cost reports become queryable again after reactivation

## HTTP Status Codes

| Status | Scenario | Meaning |
|--------|----------|---------|
| 200 | New entity created | Successfully created new entity |
| 200 | Deleted entity reactivated | Successfully reactivated deleted entity |
| 409 | Active entity exists | Cannot create - entity already active |
| 400 | Validation error | Invalid entity_id or entity_name |
| 500 | Server error | Unexpected error during creation |

## Testing

Test the reactivation flow:

```bash
# 1. Create entity
curl -X POST http://localhost:8000/api/entities \
  -d '{"entity_id": "test_123", "entity_name": "Test Entity"}'

# 2. Verify it exists
curl http://localhost:8000/api/entities/test_123

# 3. Delete it
curl -X DELETE http://localhost:8000/api/entities/test_123

# 4. Try to create with same ID (should now work)
curl -X POST http://localhost:8000/api/entities \
  -d '{"entity_id": "test_123", "entity_name": "Test Entity Restored"}'
# ✓ Should return 200 (reactivated)

# 5. Verify it's active again
curl http://localhost:8000/api/entities/test_123
```

## Backward Compatibility

✅ **Fully backward compatible**
- Existing code continues to work
- New reactivation feature is automatic
- Only change: "already exists" now returns 409 instead of 200 (HTTP standard)

## Migration from Old Behavior

If you relied on status code 200 for "already exists":

```python
# Old code
response = client.post("/api/entities", json=entity_data)
if "already exists" in response.text:  # Checked body content
    # Handle exists
```

**Update to**:

```python
# New code
response = client.post("/api/entities", json=entity_data)
if response.status_code == 409:  # Check status code
    # Handle already exists
elif response.status_code == 200:
    # Handle successful create or reactivation
```

## FAQ

**Q: What happens to sessions when entity is reactivated?**
A: Sessions remain `inactive` but become queryable again. You can manually reactivate them if needed via a separate session restoration endpoint.

**Q: Are documents preserved after reactivation?**
A: Yes! All documents, chunks, and embeddings are preserved during deletion and reactivation.

**Q: Can I prevent reactivation of specific deleted entities?**
A: Not built-in, but you could add a `permanent_delete` flag if needed.

**Q: What if I want truly permanent deletion?**
A: Currently, deletion is always soft (status="inactive"). A hard delete option could be added if needed.

## Related Documentation

- [SOFT_DELETE_ARCHITECTURE.md](SOFT_DELETE_ARCHITECTURE.md) - Soft delete design
- [DELETED_ITEMS_NON_QUERYABLE.md](DELETED_ITEMS_NON_QUERYABLE.md) - Query filtering for deleted items
- [REQUEST_TRACKING.md](REQUEST_TRACKING.md) - Request tracking overview
