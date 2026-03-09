# Security Policy

## Overview

AgenticRAG implements comprehensive security measures to protect data and ensure safe operation in production environments.

## Authentication & Authorization

### API Key Management

- **Hashing Algorithm**: PBKDF2-SHA256 (NIST-approved as of 2023)
- **Iterations**: 480,000 (recommended for key derivation)
- **Salt**: Fixed but can be rotated
- **Storage**: Hash stored in MongoDB, never raw keys
- **Masking**: API keys shown masked in logs (first 8 + last 4 chars)

### Verification Flow

```
1. User sends X-API-Key header with request
2. System hashes incoming key: pbkdf2_hmac("sha256", key, salt, 480000)
3. Hash matched against stored values in database
4. User ID and role returned if match found
5. Deterministic hashing allows database lookups
```

### Role-Based Access Control

- **Admin Role**: Can generate/delete API keys, manage users
- **User Role**: Can access knowledge bases, perform searches
- **Scope**: All operations logged with user_id for audit trails

## Database Security

### MongoDB Best Practices

- Use MongoDB Atlas for managed database
- Enable authentication with strong credentials
- Use connection pooling (implemented via PyMongo)
- Network access restricted to application servers
- Regular backups with point-in-time recovery
- Encryption at rest (Atlas default)

### RDS (Audit Logging)

- Use AWS RDS with encryption enabled
- Restrict security group to application subnets
- Enable automated backups (min 7 days retention)
- Enable Enhanced Monitoring
- Use VPC endpoints for private connectivity

## API Security

### HTTPS/TLS

```nginx
# Use reverse proxy (Nginx) for TLS termination
upstream api {
    server localhost:8000;
}

server {
    listen 443 ssl http2;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://api;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

### Request Validation

- All inputs validated with Pydantic models
- Type checking and constraints enforced
- File uploads scanned and validated
- Maximum file sizes enforced

### Rate Limiting Recommendations

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Limit per IP: 100 requests/minute
# Limit per API key: 10,000 requests/hour
```

## Data Protection

### File Storage (S3)

- Enable versioning on S3 bucket
- Enable server-side encryption (SSE-S3 or SSE-KMS)
- Block public access to bucket
- Use bucket policies for access control
- Enable CloudTrail for audit logs

### Encryption in Transit

- TLS 1.2+ required
- Certificate pinning for internal services
- Signed requests to S3 with SigV4

### Document Processing

- Uploaded files stored in S3, not filesystem
- Temporary files cleaned up immediately
- File type validation before processing
- Maximum file size limits enforced

## Audit Logging

### Operation Logging

All operations logged to AWS RDS with:
- Timestamp (UTC)
- User ID
- Operation type
- Request details
- Response status
- Error messages (if any)
- Duration

### Access Patterns

Monitor for:
- Failed authentication attempts (repeated failed API key hashes)
- Unusual API usage patterns (bulk operations)
- Cross-user data access attempts
- Administrative operations (user/key creation/deletion)

## Secrets Management

### Environment Variables

**Never commit secrets:**
```bash
# ❌ DO NOT COMMIT
OPENAI_API_KEY=sk-...
AWS_SECRET_ACCESS_KEY=...
MONGODB_PASSWORD=...
```

**Use secret management:**
```bash
# ✅ Use AWS Secrets Manager, Vault, or environment
export OPENAI_API_KEY=$(aws secretsmanager get-secret-value --secret-id openai-key)
export MONGODB_URI=$(aws secretsmanager get-secret-value --secret-id mongodb-uri)
```

### Local Development

- Copy `.env.example` to `.env`
- Add local secrets to `.env` (gitignored)
- Never commit `.env` file
- Use different keys for dev/staging/production

## Dependency Security

### Supply Chain

- Pin all dependency versions in `requirements.txt`
- Regular dependency audits for vulnerabilities
- Use `pip audit` or `safety` before deployment

```bash
# Check for security issues
pip audit

# Generate security report
safety check
```

### Vulnerability Monitoring

- GitHub Security Advisories enabled
- Dependabot checks for outdated packages
- Regular security updates applied

## Network Security

### VPC Configuration

```yaml
Public Subnet:
  - Load Balancer / Reverse Proxy (Nginx)

Private Subnet:
  - FastAPI Application
  - VPC Endpoints for S3, Secrets Manager

Database Subnets:
  - MongoDB Atlas (or RDS)
  - AWS RDS for operations
```

### Firewall Rules

- Restrict API port (8000) to known IPs/load balancers
- S3 access via VPC endpoints only
- Database access from application subnet only
- SSH access only from bastion host

## Security Checklist

### Before Production Deployment

- [ ] Enable HTTPS/TLS with valid certificates
- [ ] Use environment variables for all secrets
- [ ] Enable audit logging to RDS
- [ ] Configure S3 bucket encryption and versioning
- [ ] Set up CloudTrail for S3 access logging
- [ ] Implement rate limiting
- [ ] Configure CORS for API
- [ ] Enable VPC security groups with restricted access
- [ ] Set up monitoring and alerting
- [ ] Regular backup testing
- [ ] Load testing and performance validation
- [ ] Security audit completed
- [ ] Dependencies audited for vulnerabilities

### Ongoing Operations

- [ ] Monitor audit logs daily
- [ ] Review API access patterns weekly
- [ ] Update dependencies monthly
- [ ] Security patches applied immediately
- [ ] Backup integrity tested monthly
- [ ] Disaster recovery drill quarterly

## Incident Response

### Suspected API Key Compromise

1. **Immediate**: Revoke the compromised key
   ```bash
   curl -X POST http://localhost:8000/admin/api_keys/delete \
     -H "X-API-Key: admin-key" \
     -d '{"api_keys": ["compromised-key"]}'
   ```

2. **Investigation**: Review audit logs for unauthorized access
   ```sql
   SELECT * FROM operations
   WHERE api_hash = PBKDF2(compromised_key)
   ORDER BY timestamp DESC;
   ```

3. **Remediation**:
   - Generate new API key for affected user
   - Review and rollback unauthorized operations
   - Notify user and change secrets

### Data Breach

1. **Immediate**: Isolate affected systems
2. **Preserve**: Collect logs and forensic evidence
3. **Notify**: Follow GDPR/CCPA notification requirements
4. **Remediate**: Patch vulnerabilities and redeploy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

Please report security vulnerabilities responsibly via GitHub Security Advisories. Do not open public issues for security vulnerabilities.

**Steps:**
1. Navigate to [Security Advisories](https://github.com/Prabhath003/AgenticRAG/security/advisories)
2. Click "Report a vulnerability"
3. Provide detailed information about the vulnerability
4. Expected response time: 24-48 hours

## Security Contacts

- **Email**: prabhathchellingi2003@gmail.com
- **GitHub**: [@Prabhath003](https://github.com/Prabhath003)

## References

- [NIST Key Derivation](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-132.pdf)
- [OWASP API Security](https://owasp.org/www-project-api-security/)
- [OWASP Top 10](https://owasp.org/Top10/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [AWS Security Best Practices](https://aws.amazon.com/security/best-practices/)

---

**Last Updated**: March 9, 2026

For security concerns, please report responsibly via GitHub Security Advisories.
