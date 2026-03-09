# Deployment Guide: Operation Logging System

This guide explains how to deploy and configure the operation logging system in your production environment.

---

## Pre-Deployment Checklist

### Database Configuration

```python
# Ensure MongoDB collections exist
# ✓ OPERATION_LOGS_COLLECTION
# ✓ SERVICE_LOGS_COLLECTION

from src.config import Config
from src.infrastructure.database import get_db_session

with get_db_session() as db:
    # Check collections exist (MongoDB creates them automatically on first insert)
    # But you can create indexes for better performance

    # Index for operations by user
    db[Config.OPERATION_LOGS_COLLECTION].create_index("user_id_or_api_key")

    # Index for operations by type
    db[Config.OPERATION_LOGS_COLLECTION].create_index("operation_type")

    # Index for operations by status
    db[Config.OPERATION_LOGS_COLLECTION].create_index("status")

    # Compound index for user + type queries
    db[Config.OPERATION_LOGS_COLLECTION].create_index([
        ("user_id_or_api_key", 1),
        ("operation_type", 1)
    ])

    # Services index for finding by operation
    db[Config.SERVICE_LOGS_COLLECTION].create_index("operation_id")

    # Services index by type
    db[Config.SERVICE_LOGS_COLLECTION].create_index("service_type")

    # TTL index for automatic cleanup (optional)
    # Deletes documents older than 90 days
    db[Config.OPERATION_LOGS_COLLECTION].create_index(
        "created_at",
        expireAfterSeconds=90*24*60*60  # 90 days
    )
```

### Environment Variables

```bash
# Ensure these are set in your environment
MONGODB_URI="mongodb://..."
DB_NAME="your_db_name"
OPERATION_LOGS_COLLECTION="operation_logs"
SERVICE_LOGS_COLLECTION="service_logs"
```

### Configuration File

```python
# src/config.py
from dataclasses import dataclass

@dataclass
class Config:
    OPERATION_LOGS_COLLECTION = "operation_logs"
    SERVICE_LOGS_COLLECTION = "service_logs"
    # ... other config
```

---

## Deployment Steps

### 1. Update FastAPI Main File

```python
# api/main.py
from fastapi import FastAPI
from src.infrastructure.operation_logging import operation_endpoint, log_service
from src.core.management.core_models import OperationType, ServiceType

app = FastAPI()

# Now all endpoints can use the decorator
@app.post("/api/knowledge-base/create")
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_kb(request, api_key: str):
    # Operation tracking automatic
    log_service(ServiceType.MONGODB, 0.02)
    return {"success": True}
```

### 2. Create Database Indexes (One-time)

```bash
# Run this once after deployment
python -c "
from src.infrastructure.operation_logging import setup_db_indexes
setup_db_indexes()
print('✓ Indexes created')
"
```

### 3. Configure Logging (Optional)

```python
# src/log_creator.py
import logging

logger = logging.getLogger(__name__)

# Set log level for operation_logging
logging.getLogger('src.infrastructure.operation_logging').setLevel(logging.DEBUG)

# Or set level based on environment
import os
if os.getenv('ENVIRONMENT') == 'production':
    logging.getLogger('src.infrastructure.operation_logging').setLevel(logging.INFO)
else:
    logging.getLogger('src.infrastructure.operation_logging').setLevel(logging.DEBUG)
```

### 4. Update Endpoints Gradually

Start with critical endpoints:

```python
# Phase 1: High-traffic endpoints
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_kb(...):
    pass

# Phase 2: Data modification endpoints
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_kb(...):
    pass

# Phase 3: Background task endpoints
@operation_endpoint(OperationType.KNOWLEDGE_BASE_BUILD, auto_complete=False)
async def trigger_build(...):
    pass
```

---

## Monitoring & Observability

### Health Check Endpoint

```python
@app.get("/health/operation-logging")
async def check_operation_logging():
    """Verify operation logging system is working"""
    from src.infrastructure.database import get_db_session
    from src.config import Config

    try:
        with get_db_session() as db:
            # Try to read a recent operation
            op = db[Config.OPERATION_LOGS_COLLECTION].find_one(
                sort=[("created_at", -1)]
            )
            return {
                "status": "healthy",
                "last_operation": op["_id"] if op else None
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }, 503
```

### Metrics Collection

```python
# Create a metrics endpoint for monitoring
@app.get("/metrics/operations")
async def operation_metrics():
    """Get operation metrics for monitoring"""
    from src.infrastructure.database import get_db_session
    from src.config import Config
    from datetime import datetime, timedelta, timezone

    with get_db_session() as db:
        # Last 24 hours
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        # Count operations by status
        stats = db[Config.OPERATION_LOGS_COLLECTION].aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "avg_cost": {"$avg": "$estimated_cost_usd"},
                "total_cost": {"$sum": "$estimated_cost_usd"}
            }}
        ])

        return {
            "period": "last_24_hours",
            "operations": list(stats)
        }
```

### Logging Best Practices

```python
# In your logger configuration
logging.getLogger('src.infrastructure.operation_logging').setLevel(logging.INFO)

# Logs will show:
# 2025-12-03 10:00:00 INFO: Operation completed: op-123
# 2025-12-03 10:00:00 DEBUG: Service logged: svc-1 (type: file_processor, cost: $0.15)
# 2025-12-03 10:00:01 DEBUG: Operation logged to database: op-123
```

---

## Performance Optimization

### Database Indexes

```python
# These indexes are essential for performance
db[OPERATION_LOGS_COLLECTION].create_index([
    ("user_id_or_api_key", 1),
    ("created_at", -1)
])  # For user dashboard queries

db[SERVICE_LOGS_COLLECTION].create_index([
    ("operation_id", 1),
    ("service_type", 1)
])  # For service breakdown queries
```

### Connection Pooling

```python
# In your database configuration
from pymongo import MongoClient

client = MongoClient(
    mongodb_uri,
    maxPoolSize=50,  # Adjust based on load
    minPoolSize=10
)
```

### Batch Operations (Optional)

For high-volume scenarios, consider batching:

```python
# Using MongoDB bulk operations
from pymongo import InsertOne

operations = []
for service in services:
    operations.append(InsertOne(service.model_dump_bson()))

if operations:
    db[Config.SERVICE_LOGS_COLLECTION].bulk_write(operations)
```

---

## Troubleshooting

### Issue: Enum values stored as objects instead of strings

**Symptom**: Operations stored with `operation_type: {...}` instead of string

**Solution**: Ensure using `model_dump_bson()`:
```python
# ❌ Wrong
db[collection].insert_one(operation.model_dump())

# ✓ Correct
db[collection].insert_one(operation.model_dump_bson())
```

### Issue: Operation not logged when expected

**Symptom**: No operation document in database

**Cause**: auto_complete=False used but mark_operation_complete() not called

**Solution**: Always call mark_operation_complete() in deferred tasks:
```python
try:
    # ... work ...
    mark_operation_complete()
except Exception as e:
    mark_operation_failed(str(e))
```

### Issue: Operations not linked to services

**Symptom**: Service documents have operation_id but endpoint has no way to get services

**Solution**: Query by operation_id:
```python
services = db[SERVICE_LOGS_COLLECTION].find({
    "operation_id": operation_id
})
```

### Issue: High memory usage

**Symptom**: Memory usage grows over time

**Cause**: Context variables not being cleaned up

**Solution**: This is normal and managed by Python's garbage collector. If persistent:
1. Check for circular references in metadata
2. Ensure background tasks don't create infinite context nesting

### Issue: Slow database inserts

**Symptom**: Insert operations taking too long

**Solution**: Add indexes:
```python
db[OPERATION_LOGS_COLLECTION].create_index("created_at")
db[SERVICE_LOGS_COLLECTION].create_index("operation_id")
```

---

## Data Retention Policy

### Archiving Old Operations

```python
# Archive operations older than 1 year to cheaper storage
from datetime import datetime, timedelta, timezone

with get_db_session() as db:
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)

    # Archive old operations
    old_ops = db[Config.OPERATION_LOGS_COLLECTION].find({
        "created_at": {"$lt": cutoff}
    })

    # Transfer to archive collection or storage
    archive = db["operation_logs_archive"]
    for op in old_ops:
        archive.insert_one(op)
        db[Config.OPERATION_LOGS_COLLECTION].delete_one({"_id": op["_id"]})
```

### Cleanup Strategy

```python
# Option 1: TTL Index (automatic)
db[Config.OPERATION_LOGS_COLLECTION].create_index(
    "created_at",
    expireAfterSeconds=365*24*60*60  # 1 year
)

# Option 2: Scheduled cleanup job
import schedule

def cleanup_old_operations():
    """Delete operations older than 2 years"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=730)
    with get_db_session() as db:
        result = db[Config.OPERATION_LOGS_COLLECTION].delete_many({
            "created_at": {"$lt": cutoff}
        })
        print(f"Deleted {result.deleted_count} old operations")

schedule.every().day.at("02:00").do(cleanup_old_operations)
```

---

## Production Checklist

- [ ] MongoDB collections exist and are indexed
- [ ] All FastAPI endpoints have @operation_endpoint decorator
- [ ] api_key parameter is extracted by decorator
- [ ] Deferred tasks call mark_operation_complete() or mark_operation_failed()
- [ ] Background tasks copy context with copy_context()
- [ ] All database inserts use model_dump_bson()
- [ ] Logging is configured appropriately
- [ ] Health check endpoint works
- [ ] Metrics endpoint shows data
- [ ] Database connection pooling is configured
- [ ] TTL or cleanup policy is in place
- [ ] Monitoring is set up for error rates
- [ ] Team is trained on the system

---

## Rollback Plan

If issues arise:

### 1. Disable Operation Logging

```python
# Temporarily disable by removing decorators
# ❌ @operation_endpoint(OperationType.GET_KB)
async def get_kb(...):
    pass
```

### 2. Disable Service Logging

```python
# Temporarily disable service logging
# ❌ log_service(ServiceType.MONGODB, 0.01)
```

### 3. Clear Collections (if needed)

```python
# ⚠️ Only in non-production environments
with get_db_session() as db:
    db[Config.OPERATION_LOGS_COLLECTION].drop()
    db[Config.SERVICE_LOGS_COLLECTION].drop()
```

---

## Support & Resources

- **Documentation**: See README.md and IMPLEMENTATION_SUMMARY.md
- **Usage Guide**: See USAGE_BSON_MODELS.md
- **Developer Checklist**: See DEVELOPER_CHECKLIST.md
- **Troubleshooting**: See README.md troubleshooting section

---

## Feedback & Improvements

Track issues and improvements:

```python
# Log issues or improvement suggestions
# GitHub Issues: [Your repo URL]
# Contact: [Your team contact]
```

---

## Version History

### v1.0 (Current)
- Deferred completion pattern
- BSON optimization
- Automatic scope detection
- Context variable support
- Full documentation

**Next versions will include:**
- [ ] Built-in analytics dashboards
- [ ] Cost threshold alerts
- [ ] Operation tracing across services
- [ ] Distributed tracing integration
