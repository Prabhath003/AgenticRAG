import os
import json
from dotenv import load_dotenv

from ..log_creator import get_file_logger

load_dotenv()

logger = get_file_logger()


class Config:

    # MongoDB Configuration
    MONGODB_URL = os.getenv("MONGODB_URL")

    # AWS Configuration
    AWS_BEDROCK_REGION = os.getenv("AWS_BEDROCK_REGION", "us-east-1")
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

    ## AWS RDS Configuration
    AWS_RDS_HOST_URL = os.getenv("AWS_RDS_HOST_URL")
    AWS_RDS_PORT = int(os.getenv("AWS_RDS_PORT", "3306"))
    AWS_RDS_USER = os.getenv("AWS_RDS_USER", "admin")
    AWS_RDS_PASSWORD = os.getenv("AWS_RDS_PASSWORD", "")
    AWS_RDS_CHARSET = os.getenv("AWS_RDS_CHARSET", "utf8mb4")

    ## AWS S3 Configuration
    AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "titli")
    AWS_S3_ALIAS = os.getenv("AWS_S3_ALIAS")
    USE_AWS_S3_STORAGE = False  # Toggle between S3 and local storage

    # Database Configurations
    DATABASE_NAME = "agentic-rag"
    API_KEYS_COLLECTION = "api-keys"
    USERS_COLLECTION = "users"
    KNOWLEDGE_BASES_COLLECTION = "knowledge-base"
    MESSAGES = "messages"
    DOCUMENTS_COLLECTION = "documents"
    DOCUMENT_CONTENTS_COLLECTION = "doc-contents"
    CONVERSATIONS_COLLECTION = "conversations"
    ## Metrics and Usage Tracking
    SERVICE_LOGS_COLLECTION = "service-logs"  # Individual services with breakdown and cost
    OPERATION_LOGS_COLLECTION = "operation-logs"  # Operations with list of service_log_ids

    # OpenAI Configuration
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # Azure OpenAI Configuration
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
    AZURE_OPENAI_DEPLOYMENT = "gpt-4.1-mini"
    AZURE_OPENAI_VERSION = "2025-01-01-preview"

    # OPENAI LLM Configuration
    GPT_MODEL = "gpt-5-mini"

    # Local Embedding Model Configuration
    EMBEDDINGS_MODEL = "all-MiniLM-L6-v2"
    CHUNK_SIZE = 1000
    CHUNK_OVERLAP = 200

    # Oxylabs Configuration
    OXYLABS_API_KEY = os.getenv("OXYLABS_API_KEY")
    OXYLABS_USERNAME = os.getenv("OXYLABS_USERNAME")
    OXYLABS_PASSWORD = os.getenv("OXYLABS_PASSWORD")
    OXYLABS_BASE_URL = "https://realtime.oxylabs.io/v1/queries"

    # Backend
    BACKEND_PORT = 8000
    DATA_DIR = "data/"
    TERMINAL_CACHE_DIR = os.path.join(DATA_DIR, "terminal_cache")

    # Model inference server
    MODEL_SERVER_URI = "http://localhost:1121/infer"

    # =============================================================================
    # Documentation Access Control Configuration
    # ===========================================================================
    # Supports wildcard patterns:
    # - Exact: /docs (matches only /docs)
    # - Prefix: /docs/* (matches /docs/anything, /docs/openapi.json, etc.)
    # - Wildcard: /admin/* (matches /admin/anything)
    # - Full wildcard: * (matches everything)
    SPECIAL_ACCESS_URL_PATHS = [
        "/docs/*",
        "/redoc/*",
        "/openapi.json",
        "/admin/api_keys/*",
        "/dev*",
    ]
    DOCS_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

    @classmethod
    def load_json_config(cls):
        config_path = os.getenv("CONFIG_PATH")
        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, "r") as f:
                    config_data = json.load(f)
                    for key, value in config_data.items():
                        setattr(cls, key, value)
            except Exception as e:
                logger.error(f"Failed to load config from {config_path}: {e}")


Config.load_json_config()
