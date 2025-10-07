# Quick Start Guide

## Start the API Server

```bash
cd /home/prabhath/AgenticRAG/api
python main.py
```

Server will start on: `http://localhost:8000`

## Test the API

In another terminal:

```bash
cd /home/prabhath/AgenticRAG/api
python test_api.py
```

## Access Documentation

- Swagger UI: http://localhost:8000/docs
- Health Check: http://localhost:8000/health

## Data Storage

All data is stored in: `/home/prabhath/AgenticRAG/data/`

```
data/
├── api_storage/           # Persistent API data
│   ├── entities.json      # All entities (auto-created)
│   └── chat_sessions.json # All chat sessions (auto-created)
├── uploads/               # Uploaded files
├── entity_scoped/         # Entity-scoped RAG indexes
└── logs/                  # Application logs
```

## First Time Setup

If this is the first time running:

1. Make sure `data/api_storage/` directory exists
2. Remove any incorrectly created files/directories:
   ```bash
   rm -rf data/api_storage/entities.json data/api_storage/chat_sessions.json
   ```
3. Start the server - it will create the JSON files automatically

## Quick Test

```bash
# Create an entity
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "test_001", "entity_name": "Test Corp"}'

# Get the entity
curl http://localhost:8000/api/entities/test_001

# Check health
curl http://localhost:8000/health
```

## Troubleshooting

### Issue: "Is a directory" error

**Fix**:
```bash
rm -rf data/api_storage/entities.json data/api_storage/chat_sessions.json
# Restart server
```

### Issue: Server won't start

**Check**:
- Port 8000 is not in use: `lsof -i :8000`
- Dependencies installed: `pip install -r api/requirements.txt`
- Python environment activated

### Issue: Import errors

**Fix**:
```bash
cd api
python main.py  # Run from api directory
```

## What Persists

✅ Entities and metadata
✅ Chat sessions and messages
✅ Uploaded files
✅ FAISS indexes
✅ Document chunks

Everything survives server restarts!
