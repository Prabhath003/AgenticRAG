# AgenticRAG: Agent-Based Retrieval-Augmented Generation

A modular, production-ready agent-based RAG system with ChromaDB vector storage, S3 integration, and comprehensive agentic tool framework.

## 🌟 Key Features

### Core System
- **Agent-Based RAG**: Autonomous agents with integrated knowledge base access and tool management
- **ChromaDB Vector Storage**: GPU-enabled embeddings with vector similarity search
- **S3 Hybrid Storage**: Efficient document storage with local caching
- **Agentic Tool Framework**: Extensible tools for agents (file editing, bash, knowledge base queries)
- **Multi-Knowledge Base Support**: Scope agents to specific knowledge bases
- **File Processing**: Support for PDF, TXT, DOCX, MD files with intelligent chunking

### FastAPI Application
- **Knowledge Base Management**: Create, manage, and query knowledge bases
- **Document Ingestion**: Upload and process documents with automatic chunking
- **Semantic Search**: ChromaDB-powered vector similarity search
- **API Key Authentication**: Secure PBKDF2-hashed API key management with admin/user roles
- **Admin Endpoints**: User and API key management for multi-tenant scenarios
- **Operation Logging**: Comprehensive audit trails for all operations
- **WebSocket Support**: Real-time agent interactions

## 🚀 Quick Start

### 1. Installation

#### From Source

```bash
# Clone repository
git clone https://github.com/Prabhath003/AgenticRAG.git
cd AgenticRAG

# Install dependencies
pip install -r requirements.txt

# Or install in development mode
pip install -e .
```

### 2. Configuration

Set up environment variables:

```bash
# Copy example env file
cp .env.example .env

# Edit with your settings
MONGODB_URI=mongodb://localhost:27017
CHROMADB_PERSISTENCE_DIR=./chroma_data
AWS_S3_BUCKET=your-bucket-name
OPENAI_API_KEY=your-key-here
```

### 3. Start the API Server

```bash
cd api
python main.py
```

Server runs on: `http://localhost:8000`

### 4. Access API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## 📚 Complete Workflow Example

### Using Python with API Key

```python
import requests

BASE_URL = "http://localhost:8000"
API_KEY = "your-api-key-here"
HEADERS = {"X-API-Key": API_KEY}

# 1. Create knowledge base
response = requests.post(f"{BASE_URL}/api/knowledge-bases",
    json={
        "name": "Company Documents",
        "description": "Internal company documents and reports"
    },
    headers=HEADERS
)
kb_id = response.json()["kb_id"]
print(f"KB created: {kb_id}")

# 2. Upload document
with open("report.pdf", "rb") as f:
    files = {"file": f}
    response = requests.post(
        f"{BASE_URL}/api/knowledge-bases/{kb_id}/documents",
        files=files,
        headers=HEADERS
    )
    doc_id = response.json()["doc_id"]
    print(f"Document uploaded: {doc_id}")

# 3. Query documents
response = requests.post(
    f"{BASE_URL}/api/knowledge-bases/{kb_id}/search",
    json={"query": "quarterly revenue", "n_results": 5},
    headers=HEADERS
)
results = response.json()["results"]
print(f"Found {len(results)} relevant chunks")

# 4. Get agent context
response = requests.get(
    f"{BASE_URL}/api/knowledge-bases/{kb_id}",
    headers=HEADERS
)
kb_details = response.json()
print(f"KB has {kb_details['document_count']} documents")
```

### Using cURL

```bash
API_KEY="your-api-key-here"

# Create knowledge base
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "name": "Company Documents",
    "description": "Internal documents"
  }'

# Upload document
curl -X POST http://localhost:8000/api/knowledge-bases/kb_123/documents \
  -H "X-API-Key: $API_KEY" \
  -F "file=@report.pdf"

# Search
curl -X POST http://localhost:8000/api/knowledge-bases/kb_123/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "query": "revenue analysis",
    "n_results": 5
  }'
```

## 📋 API Endpoints

### Knowledge Base Management
- `POST /api/knowledge-bases` - Create knowledge base
- `GET /api/knowledge-bases` - List knowledge bases
- `GET /api/knowledge-bases/{kb_id}` - Get KB details
- `DELETE /api/knowledge-bases/{kb_id}` - Delete KB

### Document Management
- `POST /api/knowledge-bases/{kb_id}/documents` - Upload document
- `GET /api/knowledge-bases/{kb_id}/documents` - List documents in KB
- `GET /api/knowledge-bases/{kb_id}/documents/{doc_id}` - Get document chunks
- `DELETE /api/knowledge-bases/{kb_id}/documents/{doc_id}` - Delete document

### Semantic Search
- `POST /api/knowledge-bases/{kb_id}/search` - Vector similarity search
- `POST /api/knowledge-bases/{kb_id}/chunks/{chunk_id}/context` - Get chunk with context

### Admin Operations
- `POST /admin/api_keys/generate` - Generate API key (admin only)
- `POST /admin/api_keys/list` - List API keys (admin only)
- `POST /admin/api_keys/delete` - Delete API keys (admin only)
- `POST /admin/users/create` - Create user (admin only)
- `GET /admin/users` - List users (admin only)
- `PUT /admin/users/{user_id}` - Update user (admin only)
- `DELETE /admin/users/{user_id}` - Delete user (admin only)

### System
- `GET /health` - Health check
- `GET /` - API info

## 🏗️ Architecture

```
┌──────────────────────────────────────────────┐
│           FastAPI Application                │
│  - Knowledge Base Management                 │
│  - Document Ingestion & Chunking             │
│  - Semantic Search (ChromaDB)                │
│  - Admin & User Management                   │
│  - API Key Authentication (PBKDF2)           │
└──────────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
┌──────────────┐ ┌──────────┐ ┌─────────────┐
│  Agent MCP   │ │KB Manager│ │Operation    │
│  Client      │ │          │ │ Logging     │
└──────────────┘ └──────────┘ └─────────────┘
        │            │            │
        └────────────┼────────────┘
                     ▼
        ┌──────────────────────────┐
        │  ChromaDB Vector Store   │
        │  - GPU Embeddings        │
        │  - Similarity Search     │
        │  - Metadata Filtering    │
        └──────────────────────────┘
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
┌──────────────────┐     ┌─────────────────┐
│ ChromaDB Local   │     │  S3 Storage     │
│ (persistent)     │     │  (hybrid)       │
└──────────────────┘     └─────────────────┘
                │                │
                └────────────────┘
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
┌──────────────────┐     ┌─────────────────┐
│   MongoDB        │     │  AWS RDS        │
│  (documents)     │     │ (operations)    │
└──────────────────┘     └─────────────────┘
```

## 📂 Project Structure

```
AgenticRAG/
├── api/                                      # FastAPI application
│   ├── main.py                               # API server & routes
│   ├── _authentication.py                    # API key auth (PBKDF2)
│   ├── _models.py                            # Pydantic request/response models
│   ├── _dependencies.py                      # FastAPI dependencies
│   ├── _middleware_logging.py                # Request/response logging
│   └── _routes/                              # Route modules
│       ├── _health.py, _admin.py, _knowledge_base.py
│       ├── _documents.py, _conversation.py, _operations.py
│       └── _mcp_server.py
├── src/
│   ├── config/                               # Configuration
│   │   └── _settings.py                      # Settings & environment variables
│   ├── core/
│   │   ├── _agents/                          # Agent framework
│   │   │   ├── _main_agent.py                # Main agent implementation
│   │   │   ├── _tool_manager/                # Tool management system
│   │   │   │   └── _tools/                   # Tool implementations
│   │   │   │       ├── text_editor/          # File editing tools
│   │   │   │       ├── user_kbs/             # Knowledge base tools
│   │   │   │       └── workspace/            # Workspace tools (bash, file read)
│   │   │   └── _conversation_manager/        # Conversation management
│   │   ├── _management/                      # Management layer
│   │   │   └── _sub_managers/
│   │   │       ├── _knowledge_base_manager.py
│   │   │       ├── _conversation_manager.py
│   │   │       └── _mcp_server.py
│   │   ├── _data_indexer.py                  # Document indexing pipeline
│   │   └── models/                           # Data models
│   │       ├── agent/                        # Agent-specific models
│   │       ├── operation_audit/              # Audit logging models
│   │       └── core_models.py, response_models.py
│   └── infrastructure/
│       ├── database/                         # Database connections
│       │   ├── _mongodb.py, _aws_rdsdb.py, _json_storage.py
│       │   └── Connection pooling & session management
│       ├── storage/                          # Vector & file storage
│       │   ├── _chromadb_store.py            # ChromaDB integration
│       │   └── _s3_service.py                # S3 file storage
│       ├── clients/                          # External service clients
│       ├── operation_logging/                # Audit trail logging
│       ├── utils/                            # Utilities
│       └── dynamic_thread_pool.py            # Thread pool management
├── docs/                                     # Documentation
│   ├── README.md                             # Docs navigation hub
│   ├── QUICK_START.md, DEPLOYMENT_GUIDE.md   # Getting started
│   ├── Agent/                                # Agent documentation
│   ├── API/                                  # API documentation
│   ├── Features/                             # Feature guides
│   └── Storage/                              # Storage & database docs
├── tests/                                    # Test suite
│   ├── test_duplicate_detection.py           # Duplicate chunk tests
│   ├── test_document_creation_behavior.py    # Document processing tests
│   └── More test files...
├── examples/                                 # Example usage
│   ├── api_client.py                         # API client examples
│   └── chromadb_example.py                   # ChromaDB usage
├── pyproject.toml                            # Project configuration
├── requirements.txt                          # Python dependencies
├── environment.yml                           # Conda environment
└── README.md                                 # This file
```

## 🔑 Key Technologies

- **FastAPI**: Modern async Python web framework
- **Uvicorn**: Production ASGI server
- **Pydantic**: Type validation & serialization
- **ChromaDB**: Vector database with persistent storage
- **LangChain**: Document processing & text chunking
- **OpenAI & HuggingFace**: LLM & embedding models
- **MongoDB**: Document database for KB storage
- **AWS S3**: Distributed file storage
- **AWS RDS**: Relational database for audit logs
- **PyMongo**: MongoDB driver
- **Boto3**: AWS SDK integration

## 🎯 Performance

- **Vector Search**: <100ms for semantic similarity queries
- **GPU Embeddings**: Fast embedding generation with GPU support
- **Chunking Pipeline**: Intelligent document chunking with duplicate detection
- **S3 Hybrid Storage**: Local cache with S3 backup for reliability
- **Scalable Architecture**: Support for multiple concurrent users via API key isolation
- **Async Operations**: Non-blocking I/O throughout the stack

## 📦 Package Configuration

### Build System

This project uses modern Python packaging with `pyproject.toml`. Key configuration files:

- **`pyproject.toml`**: Main project configuration including:
  - Project metadata (name, version, description, author)
  - Dependencies and optional groups (dev, docs, gpu)
  - Tool configurations (pytest, black, isort, mypy)
  - Python version requirements (3.10+)

- **`MANIFEST.in`**: Specifies which files to include in source distributions:
  - Documentation files
  - Example files
  - Configuration files
  - Excludes unnecessary files (.git, .env, cache, data)

### Optional Dependencies

```bash
# Development tools (testing, linting, formatting)
pip install -e ".[dev]"

# Documentation tools
pip install -e ".[docs]"

# GPU support (FAISS-GPU, PyTorch with CUDA)
pip install -e ".[gpu]"

# All extras
pip install -e ".[dev,docs,gpu]"
```

## ⚙️ Configuration

### Environment Variables

```bash
# API Configuration
PORT=8000
API_HOST=0.0.0.0
API_WORKERS=4

# Database
MONGODB_URI=mongodb://localhost:27017
DATABASE_NAME=agentic_rag

# ChromaDB
CHROMADB_PERSISTENCE_DIR=./chroma_data
CHROMADB_HOST=localhost
CHROMADB_PORT=8001

# AWS S3
AWS_S3_BUCKET=your-bucket-name
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=us-east-1

# LLM & Embeddings
OPENAI_API_KEY=your-api-key
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Logging
LOG_LEVEL=info
```

### Settings Configuration

Edit `src/config/_settings.py`:

```python
class Config:
    # Database
    MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "agentic_rag")

    # ChromaDB
    CHROMADB_PERSISTENCE_DIR = "./chroma_data"

    # Document Processing
    CHUNK_SIZE = 1000
    CHUNK_OVERLAP = 200

    # Collections
    CHUNKS_COLLECTION = "chunks"
    DOCUMENTS_COLLECTION = "documents"
    USERS_COLLECTION = "users"
    API_KEYS_COLLECTION = "api_keys"
```

## 🧪 Testing

### Run Test Suite

```bash
# Run all tests with pytest
pytest tests/ -v

# Run specific test file
pytest tests/test_duplicate_detection.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Manual API Testing

```bash
# Health check
curl http://localhost:8000/health

# Generate API key (requires admin key)
curl -X POST http://localhost:8000/admin/api_keys/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: admin-key" \
  -d '{"user_id": "user_123", "role": "user"}'

# Create knowledge base
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"name": "Test KB", "description": "Test"}'
```

## 🚢 Production Deployment

### Using Uvicorn

```bash
cd api
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4 --log-level info
```

### Using Docker

```bash
# Build image
docker build -t agentic-rag:latest .

# Run with environment variables
docker run -p 8000:8000 \
  -e MONGODB_URI=mongodb://mongo:27017 \
  -e AWS_S3_BUCKET=your-bucket \
  -e OPENAI_API_KEY=your-key \
  agentic-rag:latest
```

### Using Docker Compose

```bash
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop services
docker-compose down
```

### With Nginx (Reverse Proxy)

```nginx
upstream api {
    server localhost:8000;
}

server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

See [DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md) for complete deployment instructions.

## 🔒 Security

### Built-In Security Features

1. **API Key Authentication**: PBKDF2-hashed keys (NIST-approved, 480,000 iterations)
2. **Role-Based Access Control**: Admin and user roles for API keys
3. **Operation Audit Logging**: All operations logged with timestamps and user tracking
4. **Request Validation**: Pydantic models validate all inputs
5. **HTTPS Support**: Ready for reverse proxy (Nginx, CloudFlare)

### Production Recommendations

1. **HTTPS/TLS**: Use reverse proxy or load balancer
2. **Rate Limiting**: Implement per user/IP rate limiting
3. **CORS Configuration**: Restrict allowed origins
4. **Database Security**: Use MongoDB Atlas or RDS with encryption
5. **S3 Security**: Enable versioning and encryption on S3 bucket
6. **Secrets Management**: Use AWS Secrets Manager or HashiCorp Vault
7. **Monitoring**: Enable CloudWatch/DataDog monitoring

### API Key Security

```python
# API keys are hashed with PBKDF2 before storage
from api._authentication import verify_api_key_header

# Use X-API-Key header with PBKDF2 verification
# Keys are hashed: api_hash = pbkdf2_hmac("sha256", key, salt, 480000)
# Database lookups match against hashed values only
```

## 📖 Documentation

### Quick Start & Deployment
- **[QUICK_START.md](./docs/QUICK_START.md)** - Getting started guide
- **[DEPLOYMENT_GUIDE.md](./docs/DEPLOYMENT_GUIDE.md)** - Production deployment

### Agent Framework
- **[Agent MCP Client Guide](./docs/Agent/AGENT_MCP_CLIENT_GUIDE.md)** - Complete agent guide
- **[Agent MCP Quick Reference](./docs/Agent/AGENT_MCP_CLIENT_QUICK_REFERENCE.md)** - Quick lookup

### Storage & Vector Database
- **[ChromaDB Quick Start](./docs/Storage/ChromaDB/CHROMADB_QUICK_START.md)** - ChromaDB setup
- **[ChromaDB Development Guide](./docs/Storage/ChromaDB/CHROMADB_DEVELOPMENT_GUIDE.md)** - Development
- **[ChromaDB + S3 Architecture](./docs/Storage/ChromaDB/CHROMADB_S3_ARCHITECTURE.md)** - Hybrid storage

### Core Features
- **[Document Processing Pipeline](./docs/Features/DOCUMENT_PROCESSING_PIPELINE.md)** - Ingestion process
- **[Duplicate Chunk Handling](./docs/Features/DUPLICATE_CHUNK_HANDLING.md)** - Deduplication strategy
- **[Troubleshooting: Zero Chunks](./docs/Features/TROUBLESHOOTING_ZERO_CHUNKS.md)** - Common issues

### API Documentation
- **[API Documentation](./docs/API/API_DOCUMENTATION.md)** - Complete API reference
- **[Chunk Ingestion API](./docs/API/CHUNK_INGESTION_API.md)** - Chunk upload details

### Interactive API Docs
- **[Swagger UI](http://localhost:8000/docs)** - Interactive API explorer
- **[ReDoc](http://localhost:8000/redoc)** - Alternative API documentation

## 🎓 Use Cases

### 1. Multi-Tenant SaaS
Multiple customers with isolated knowledge bases and separate API keys. PBKDF2-hashed keys provide secure per-tenant access control.

### 2. Enterprise Knowledge Base
Large organizations indexing internal documentation for employees and AI agents to query.

### 3. Customer Support Platform
Knowledge bases per customer with RAG-powered responses from their specific documents and history.

### 4. Research & Document Analysis
Analyze large document collections (research papers, reports) with semantic search and agent-based insights.

### 5. Intelligent Assistant Backend
Power AI assistants with fine-grained knowledge base scoping and operation audit trails.

### 6. Data-Driven Insights
Automatically extract insights from uploaded documents using agent tools and vector search.

## 🛠️ Development

### Adding New Agent Tools

```python
# In src/core/_agents/_tool_manager/_tools/

from ._base_tool import BaseTool

class CustomTool(BaseTool):
    name = "custom_tool"
    description = "Tool description"

    async def run(self, **kwargs):
        # Tool implementation
        return {"result": "success"}
```

### Adding New API Endpoints

```python
# In api/_routes/

from fastapi import APIRouter, Depends
from .._authentication import verify_api_key_header

router = APIRouter(prefix="/api/custom", tags=["custom"])

@router.post("/endpoint")
async def custom_endpoint(data: CustomModel, user_id: str = Depends(verify_api_key_header)):
    """Authenticated custom endpoint"""
    # Implementation with user_id scope
    return {"result": "success"}
```

### Extending Knowledge Base Manager

```python
# In src/core/_management/_sub_managers/_knowledge_base_manager.py

async def custom_kb_operation(self, kb_id: str, **kwargs):
    """Custom KB operation"""
    # Use self.db for database access
    # Use ChromaDBStore for vector operations
    return result
```

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📝 License

MIT License - See LICENSE file for details

## 👨‍💻 Author

**Prabhath Chellingi**
- GitHub: [@Prabhath003](https://github.com/Prabhath003)
- Email: prabhathchellingi2003@gmail.com

## 🙏 Acknowledgments

- **ChromaDB** for modern vector database
- **FastAPI** for high-performance async web framework
- **LangChain** for document processing & LLM integration
- **OpenAI & HuggingFace** for LLM and embedding models
- **MongoDB & AWS** for reliable data storage
- **PyMongo & Boto3** for Python ecosystem integration

## 📊 Status

✅ **Production Ready**

- Agent-based RAG system with full modularity
- ChromaDB vector search with GPU support
- Secure PBKDF2 API key authentication
- MongoDB & S3 hybrid storage architecture
- Comprehensive operation audit logging
- Complete error handling & rollback mechanisms
- Full test coverage with duplicate detection
- Production deployment guides

## 🔄 Version History

### v1.0.0 (Current)
- Agent-based RAG system with modular tool framework
- ChromaDB vector storage with GPU embeddings
- S3 hybrid storage for scalability
- PBKDF2-hashed API key authentication
- Admin/user role-based access control
- Operation audit logging with timestamps
- Comprehensive documentation and examples
- Duplicate chunk detection & handling

## 🎯 Roadmap

- [ ] Advanced agent capabilities (multi-turn reasoning, tool chaining)
- [ ] Vector search performance optimizations
- [ ] Knowledge base tagging and categorization
- [ ] Rate limiting & quota management
- [ ] Webhook events for operation notifications
- [ ] Batch document processing
- [ ] Analytics dashboard
- [ ] Multi-language support
- [ ] Semantic caching layer

## 📞 Support

- **Documentation**: [docs/README.md](./docs/README.md) - Complete docs index
- **Issues**: [GitHub Issues](https://github.com/Prabhath003/AgenticRAG/issues)
- **Email**: prabhathchellingi2003@gmail.com
- **API Docs**: http://localhost:8000/docs (Swagger UI)

---

**Built with ❤️ by Prabhath Chellingi**

**🚀 Production-Ready Agent-Based RAG System**
