# Entity Reactivation - Quick Reference

## TL;DR

**You can now reuse deleted entity IDs!** When you create an entity with a deleted entity_id, it automatically reactivates instead of throwing "already exists" error.

## Three Scenarios

### Scenario 1: New Entity (First Time)
```bash
POST /api/entities
  entity_id: "company_123" (new)

✓ Creates new entity
→ 200 OK
```

### Scenario 2: Active Entity Exists
```bash
POST /api/entities
  entity_id: "company_123" (already active)

✗ Conflict!
→ 409 Conflict (Entity already exists)
```

### Scenario 3: Deleted Entity Exists
```bash
# Previous flow:
DELETE /api/entities/company_123  # Deleted

# New flow:
POST /api/entities
  entity_id: "company_123" (was deleted)

✓ REACTIVATES the deleted entity
→ 200 OK
```

## API Response Codes

| Code | Scenario |
|------|----------|
| 200 | ✓ Created new entity OR reactivated deleted entity |
| 409 | ✗ Active entity already exists (conflict) |
| 400 | ✗ Validation error |
| 500 | ✗ Server error |

## What Gets Reactivated?

When you reactivate a deleted entity:

✓ **Preserved**
- All documents
- All indexed chunks
- Vector embeddings
- All request metrics
- Cost history

✓ **Updated**
- `entity_name` (new value)
- `description` (if provided)
- `metadata` (if provided)

✓ **Tracked**
- `created_at` (original timestamp)
- `deactivated_at` (deletion timestamp)
- `reactivated_at` (new reactivation timestamp)

## Usage Example

```bash
# 1. Create entity
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "entity_name": "TechCorp Industries"
  }'
# Response: 200 OK - Created

# 2. Upload files, run queries, etc.
# ...

# 3. Delete entity
curl -X DELETE http://localhost:8000/api/entities/company_123
# Response: 200 OK - Deleted

# 4. Recreate with same ID
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "entity_name": "TechCorp Industries"
  }'
# Response: 200 OK - REACTIVATED (NOT created new)
# All documents and history still there!
```

## Code Changes

**New Helper Functions**:
```python
# Get any entity (active or inactive)
get_entity_from_storage(entity_id) -> Dict | None

# Get only active entities
get_active_entity_from_storage(entity_id) -> Dict | None

# Get only deleted entities
get_inactive_entity_from_storage(entity_id) -> Dict | None
```

**Modified Endpoint**:
```python
POST /api/entities
# Now handles:
# 1. Check if active → reject (409)
# 2. Check if inactive → reactivate (200)
# 3. Neither → create (200)
```

## Decision Tree

```
POST /api/entities?entity_id=X
        ↓
Is X active?
├─ YES → 409 Conflict (already exists)
└─ NO  → Continue
        ↓
Is X inactive (deleted)?
├─ YES → Reactivate & return 200
└─ NO  → Create new & return 200
```

## Key Changes from Before

| Before | After |
|--------|-------|
| Create with deleted ID → Error | Create with deleted ID → Reactivates |
| "already exists" → status 200 | "already exists" → status 409 |
| Error message in body | Error object in JSON response |

## Error Handling

### Old Code
```python
response = client.post("/api/entities", json=data)
if "already exists" in response.text:
    # Checked text content
```

### New Code
```python
response = client.post("/api/entities", json=data)
if response.status_code == 409:
    # Check HTTP status code
    error = response.json()
    print(f"Entity exists: {error['error']}")
elif response.status_code == 200:
    # Created or reactivated
    entity = EntityResponse(**response.json())
```

## Benefits

✅ No more "Entity already exists" errors when reusing IDs
✅ Automatic data preservation and restoration
✅ Clean way to recover accidentally deleted entities
✅ Better tenant churn handling (pause/resume accounts)
✅ Transparent to user (looks like normal create)

## Storage Details

```json
Active Entity:
{
  "entity_id": "company_123",
  "status": "active",
  "created_at": "2025-01-15T10:30:00Z",
  "documents": [...]
}

Deleted Entity:
{
  "entity_id": "company_123",
  "status": "inactive",
  "created_at": "2025-01-15T10:30:00Z",
  "deactivated_at": "2025-01-15T11:00:00Z",
  "documents": [...]  ← Still there!
}

Reactivated Entity:
{
  "entity_id": "company_123",
  "status": "active",  ← Back to active
  "created_at": "2025-01-15T10:30:00Z",  ← Original time
  "deactivated_at": "2025-01-15T11:00:00Z",
  "reactivated_at": "2025-01-15T11:30:00Z",  ← New timestamp
  "documents": [...]  ← Back to active!
}
```

## Testing

```bash
# Test reactivation
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_123", "entity_name": "Test"}'
# → 200 Created

curl -X DELETE http://localhost:8000/api/entities/test_123
# → 200 Deleted

curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_123", "entity_name": "Test Restored"}'
# → 200 Reactivated ✓
```

## Migration Checklist

- [ ] Review entity creation code
- [ ] Update status code checks (409 instead of 200 for conflicts)
- [ ] Update error handling (check status codes, not text)
- [ ] Test reactivation flow
- [ ] Update any API client libraries

## Backward Compatibility

✅ Fully backward compatible
- Existing code for active entities works unchanged
- Only change: "already exists" returns 409 (HTTP standard)
- Reactivation is transparent (user doesn't need to handle it)

## FAQ

**Q: Can I disable reactivation?**
A: Not currently. Reactivation happens automatically. If you need hard deletion, let us know.

**Q: Are sessions also reactivated?**
A: Sessions stay inactive but become queryable again. Manually reactivate if needed.

**Q: What about cost history?**
A: All costs become queryable again after reactivation.

**Q: Can I track when entity was reactivated?**
A: Yes! Check `reactivated_at` field in entity data.

---

**File**: [ENTITY_REACTIVATION.md](ENTITY_REACTIVATION.md) for complete documentation
