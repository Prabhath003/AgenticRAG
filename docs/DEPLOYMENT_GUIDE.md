# Entity-Scoped RAG API - Deployment Guide

## Quick Start (Development)

### 1. Install Dependencies

```bash
# Install core dependencies
pip install -r requirements.txt

# Install API dependencies
cd api
pip install -r requirements.txt
cd ..
```

### 2. Start the Server

```bash
cd api
python main.py
```

The server will start on `http://localhost:8000`

### 3. Access API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## Testing the API

### Using the Test Client

```bash
cd api
python test_api.py
```

### Using cURL

```bash
# Health check
curl http://localhost:8000/health

# Create entity
curl -X POST http://localhost:8000/api/entities \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "entity_name": "TechCorp Industries",
    "description": "AI analytics company"
  }'

# Upload file
curl -X POST http://localhost:8000/api/entities/company_123/files \
  -F "file=@/path/to/document.pdf" \
  -F "description=Annual Report 2024"

# Create chat session
curl -X POST http://localhost:8000/api/chat/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "session_name": "Financial Analysis"
  }'

# Send message
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_xyz789",
    "message": "What are the key financial metrics?",
    "stream": false
  }'
```

## Production Deployment

### Option 1: Using Uvicorn (Recommended)

```bash
# Install uvicorn with production extras
pip install uvicorn[standard] gunicorn

# Run with multiple workers
cd api
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --log-level info
```

### Option 2: Using Gunicorn with Uvicorn Workers

```bash
cd api
gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --log-level info
```

### Option 3: Docker Deployment

Create `api/Dockerfile`:

```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy core source
COPY ../src ./src

# Copy API
COPY . ./api

WORKDIR /app/api

# Expose port
EXPOSE 8000

# Run server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

Build and run:

```bash
docker build -t entity-scoped-rag-api .
docker run -p 8000:8000 -v $(pwd)/data:/app/data entity-scoped-rag-api
```

### Option 4: Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  api:
    build: ./api
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - DATA_DIR=/app/data
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - LOG_LEVEL=info
    restart: unless-stopped
```

Run:

```bash
docker-compose up -d
```

## Environment Variables

Create `.env` file:

```bash
# Required
OPENAI_API_KEY=your-api-key-here

# Optional
DATA_DIR=/path/to/data
LOG_LEVEL=info
PORT=8000
WORKERS=4
```

## Nginx Reverse Proxy (Production)

Create `/etc/nginx/sites-available/rag-api`:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support for streaming
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts for long-running requests
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/rag-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## SSL/HTTPS with Let's Encrypt

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal (already configured)
sudo certbot renew --dry-run
```

## Systemd Service (Production)

Create `/etc/systemd/system/rag-api.service`:

```ini
[Unit]
Description=Entity-Scoped RAG API
After=network.target

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/opt/AgenticRAG/api
Environment="PATH=/opt/AgenticRAG/venv/bin"
Environment="DATA_DIR=/var/lib/rag-data"
ExecStart=/opt/AgenticRAG/venv/bin/uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rag-api
sudo systemctl start rag-api
sudo systemctl status rag-api
```

## Performance Tuning

### 1. Worker Configuration

```bash
# Calculate optimal workers
# Formula: (2 x CPU cores) + 1
workers=$((2 * $(nproc) + 1))

uvicorn main:app --workers $workers
```

### 2. File Upload Limits

In `main.py`, add:

```python
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI()

# Limit file upload size (100MB)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]
)
```

### 3. CORS Configuration (Production)

Update in `main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://your-domain.com",
        "https://app.your-domain.com"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
```

## Monitoring

### Health Check Endpoint

```bash
# Monitor with curl
while true; do
  curl -s http://localhost:8000/health | jq .
  sleep 30
done
```

### Logging

Logs are written to `data/logs/` by default.

View logs:

```bash
tail -f data/logs/app.log
```

### Prometheus Metrics (Optional)

Add to requirements.txt:

```
prometheus-fastapi-instrumentator
```

Update `main.py`:

```python
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI()

# Expose metrics
Instrumentator().instrument(app).expose(app)
```

Metrics available at: `http://localhost:8000/metrics`

## Security Considerations

### 1. Add Authentication

```python
from fastapi import Security, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != "your-secret-token":
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials

@app.post("/api/entities")
async def create_entity(entity: EntityCreate, token: str = Security(verify_token)):
    # Your code here
    pass
```

### 2. Rate Limiting

```bash
pip install slowapi
```

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: ChatRequest):
    pass
```

### 3. File Upload Validation

```python
ALLOWED_EXTENSIONS = {'.pdf', '.txt', '.docx', '.md'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

async def validate_file(file: UploadFile):
    # Check extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type {ext} not allowed")

    # Check size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large")

    await file.seek(0)
    return content
```

## Backup Strategy

### 1. Data Backup

```bash
#!/bin/bash
# backup.sh

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/rag-data"

# Backup data directory
tar -czf "$BACKUP_DIR/data_$DATE.tar.gz" /path/to/data/

# Keep last 7 days
find "$BACKUP_DIR" -name "data_*.tar.gz" -mtime +7 -delete
```

### 2. Automated Backups (Cron)

```bash
# Add to crontab
crontab -e

# Backup daily at 2 AM
0 2 * * * /opt/scripts/backup.sh
```

## Troubleshooting

### Issue: Server won't start

```bash
# Check port availability
sudo lsof -i :8000

# Check logs
tail -f data/logs/app.log

# Verify dependencies
pip list | grep fastapi
pip list | grep uvicorn
```

### Issue: Out of memory

```bash
# Reduce workers
uvicorn main:app --workers 2

# Monitor memory
watch -n 1 free -h
```

### Issue: Slow responses

```bash
# Check entity stats
curl http://localhost:8000/health

# Monitor system resources
htop

# Enable debug logging
export LOG_LEVEL=debug
```

## Complete Production Checklist

- [ ] Install all dependencies
- [ ] Set environment variables
- [ ] Configure CORS for your domain
- [ ] Add authentication/authorization
- [ ] Add rate limiting
- [ ] Configure file upload limits
- [ ] Set up Nginx reverse proxy
- [ ] Enable SSL/HTTPS
- [ ] Create systemd service
- [ ] Set up monitoring/logging
- [ ] Configure automated backups
- [ ] Test all endpoints
- [ ] Load testing
- [ ] Security audit

## Support

- **Documentation**: [API_DOCUMENTATION.md](./API_DOCUMENTATION.md)
- **Author**: Prabhath Chellingi
- **Email**: prabhathchellingi200@gmail.com
- **API Docs**: http://localhost:8000/docs

---

**Ready for production deployment! 🚀**
