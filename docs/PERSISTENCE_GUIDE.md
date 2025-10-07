# Data Persistence Guide

## Overview

The Entity-Scoped RAG API now has **complete data persistence** using JSON storage. All data survives server restarts and is stored in `Config.DATA_DIR`.

## What's Persisted

### 1. Entities ✅
**Storage Location**: `data/api_storage/entities.json`

All entity information is persisted including:
- Entity ID and name
- Description and metadata
- Document list (doc_id, filename, file_path)
- Creation timestamp

**Survives Server Restart**: Yes ✅

### 2. Chat Sessions ✅
**Storage Location**: `data/api_storage/chat_sessions.json`

All chat session data is persisted including:
- Session ID and entity ID
- Session name and metadata
- Complete message history
- Creation and last activity timestamps

**Survives Server Restart**: Yes ✅

### 3. Uploaded Files ✅
**Storage Location**: `data/uploads/{entity_id}/`

Physical files uploaded through the API are saved to disk.

**Survives Server Restart**: Yes ✅

### 4. Entity-Scoped FAISS Indexes ✅
**Storage Location**: `data/entity_scoped/entities/{entity_id}/vector_store/`

FAISS vector indexes for each entity are automatically persisted.

**Survives Server Restart**: Yes ✅

### 5. Document Chunks & Metadata ✅
**Storage Location**: `data/entity_scoped/entities/{entity_id}/`

All document chunks and metadata are persisted via the entity-scoped RAG system.

**Survives Server Restart**: Yes ✅

## Storage Architecture

```
data/
├── api_storage/                  # API persistent storage
│   ├── entities.json            # All entities
│   └── chat_sessions.json       # All chat sessions
├── uploads/                      # Uploaded files
│   ├── company_123/
│   │   ├── report.pdf
│   │   └── document.txt
│   └── company_456/
│       └── analysis.pdf
├── entity_scoped/                # Entity-scoped RAG data
│   └── entities/
│       ├── company_123/
│       │   ├── vector_store/    # FAISS index
│       │   ├── doc_to_chunks.json
│       │   └── chunk_metadata.json
│       └── company_456/
│           ├── vector_store/    # FAISS index
│           ├── doc_to_chunks.json
│           └── chunk_metadata.json
└── logs/                         # Application logs
    └── app.log
```

## How It Works

### JSONStorage with Atomic Writes

All persistent data uses the `JSONStorage` class which provides:

1. **Atomic Writes**: Data is written to a temporary file and then atomically renamed to prevent corruption
2. **File Locks**: Per-file thread locks ensure safe concurrent access
3. **MongoDB-like API**: Familiar find/update/delete operations
4. **Cross-Platform**: Works on Windows and POSIX systems

### Automatic Persistence

Every operation automatically persists data:

```python
# Creating an entity
POST /api/entities
→ Saves to data/api_storage/entities.json

# Uploading a file
POST /api/entities/{entity_id}/files
→ Saves file to data/uploads/{entity_id}/
→ Indexes to data/entity_scoped/entities/{entity_id}/
→ Updates entity in entities.json

# Creating a chat session
POST /api/chat/sessions
→ Saves to data/api_storage/chat_sessions.json

# Sending a message
POST /api/chat
→ Immediately saves user message to chat_sessions.json
→ Saves assistant response when complete
```

## Server Restart Behavior

### What Happens on Restart

1. **Entities**: Loaded from `entities.json` ✅
2. **Chat Sessions**: Loaded from `chat_sessions.json` ✅
3. **Files**: Already on disk in `uploads/` ✅
4. **FAISS Indexes**: Lazy-loaded when needed ✅
5. **Research Agents**: Reconstructed on-demand ✅

### Research Agent Reconstruction

Research agents are **not** persisted but are automatically reconstructed when needed:

```python
# When accessing a session after restart
session_data = get_chat_session_from_storage(session_id)

# Agent is reconstructed if not in memory
if session_id not in session_agents:
    entity_data = get_entity_from_storage(session_data["entity_id"])
    agent = ResearchAgent(
        id=session_data["entity_id"],
        entity_name=entity_data["entity_name"],
        use_entity_scoped=True
    )
    session_agents[session_id] = agent
```

## Testing Persistence

### Test Scenario 1: Entity Persistence

```bash
# 1. Start server
cd api && python main.py

# 2. Create entity
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_001", "entity_name": "Test Corp"}'

# 3. Stop server (Ctrl+C)

# 4. Restart server
python main.py

# 5. Verify entity exists
curl http://localhost:8000/api/entities/test_001
# ✅ Returns entity data
```

### Test Scenario 2: Chat Session Persistence

```bash
# 1. Create entity and upload file
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_002", "entity_name": "Test2"}'

curl -X POST http://localhost:8000/api/entities/test_002/files \
  -F "file=@document.txt"

# 2. Create chat session
curl -X POST http://localhost:8000/api/chat/sessions \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_002", "session_name": "Test Chat"}'
# Returns: {"session_id": "session_abc123", ...}

# 3. Send a message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "session_abc123", "message": "Hello", "stream": false}'

# 4. Stop and restart server

# 5. Get chat history
curl http://localhost:8000/api/chat/sessions/session_abc123/messages
# ✅ Returns all messages including "Hello"

# 6. Continue chatting
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "session_abc123", "message": "Continue", "stream": false}'
# ✅ Works! Agent is reconstructed automatically
```

### Test Scenario 3: File Upload Persistence

```bash
# 1. Upload file
curl -X POST http://localhost:8000/api/entities/test_001/files \
  -F "file=@report.pdf"
# Returns: {"doc_id": "doc_xyz789", ...}

# 2. Stop server

# 3. Verify files on disk
ls data/uploads/test_001/
# ✅ report.pdf exists

ls data/entity_scoped/entities/test_001/
# ✅ vector_store/ directory exists

# 4. Restart server

# 5. Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_001", "query": "revenue", "k": 5}'
# ✅ Returns search results (FAISS index loaded)

# 6. List files
curl http://localhost:8000/api/entities/test_001/files
# ✅ Shows report.pdf with doc_id
```

## Data Backup Strategy

### Manual Backup

```bash
# Backup all data
tar -czf backup-$(date +%Y%m%d).tar.gz data/

# Backup only API storage
tar -czf api-backup-$(date +%Y%m%d).tar.gz data/api_storage/

# Backup specific entity
tar -czf entity-company_123-$(date +%Y%m%d).tar.gz \
    data/uploads/company_123/ \
    data/entity_scoped/entities/company_123/
```

### Automated Backup Script

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/backups/rag-api"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup data directory
tar -czf "$BACKUP_DIR/data_$DATE.tar.gz" data/

# Keep last 7 days
find "$BACKUP_DIR" -name "data_*.tar.gz" -mtime +7 -delete

echo "Backup completed: $BACKUP_DIR/data_$DATE.tar.gz"
```

Add to crontab for daily backups:
```bash
crontab -e
# Add: 0 2 * * * /path/to/backup.sh
```

## Restore from Backup

```bash
# Stop server
# Ctrl+C or: kill $(pgrep -f "python main.py")

# Restore data
tar -xzf backup-20250106.tar.gz

# Restart server
cd api && python main.py

# All data restored! ✅
```

## Migration Guide

If you have an existing deployment with in-memory storage, the first restart will:

1. Create empty `entities.json` and `chat_sessions.json` files
2. Start with no entities or sessions
3. All new data will be persisted

**No migration is needed** - just restart the server and start fresh!

## Performance Considerations

### JSONStorage Performance

- **Read Operations**: Fast (loaded into memory)
- **Write Operations**: Atomic writes with file locks
- **Concurrent Access**: Thread-safe with per-file locks

### Scaling Recommendations

For **< 1000 entities**: JSONStorage is perfect ✅

For **> 1000 entities**: Consider migrating to:
- PostgreSQL for entities and chat sessions
- Keep entity-scoped FAISS on disk
- Keep uploaded files on disk or object storage (S3)

## Troubleshooting

### Issue: Data Not Persisting

**Check**:
```bash
# Verify storage directory exists
ls -la data/api_storage/

# Check file permissions
ls -l data/api_storage/entities.json
ls -l data/api_storage/chat_sessions.json

# Check logs
tail -f data/logs/app.log
```

**Solution**:
```bash
# Ensure directory exists and is writable
mkdir -p data/api_storage
chmod 755 data/api_storage
```

### Issue: Corrupted JSON Files

**Symptoms**: Server fails to start with JSON decode error

**Solution**:
```bash
# Check JSON validity
python -m json.tool data/api_storage/entities.json

# If corrupted, restore from backup or reset
rm data/api_storage/entities.json
# Server will create new empty file on startup
```

### Issue: Missing FAISS Index

**Symptoms**: Search returns no results after restart

**Solution**:
```bash
# FAISS indexes are lazy-loaded
# They should exist on disk:
ls data/entity_scoped/entities/company_123/vector_store/

# If missing, re-upload the documents
curl -X POST http://localhost:8000/api/entities/company_123/files \
  -F "file=@document.pdf"
```

## Best Practices

1. **Regular Backups**: Set up automated daily backups
2. **Monitor Disk Space**: Watch `data/` directory size
3. **Clean Up**: Delete old entities/sessions you don't need
4. **Test Restarts**: Regularly test server restart to ensure persistence works
5. **Logging**: Check logs for any storage errors

## Summary

✅ **All data persists** in `Config.DATA_DIR`
✅ **Server restarts** don't lose any data
✅ **Atomic writes** prevent corruption
✅ **Thread-safe** for concurrent access
✅ **Automatic** - no manual saves needed
✅ **Backup-friendly** - just tar the data/ directory

---

**Your data is safe and persistent! 🎉**
