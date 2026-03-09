import hashlib
import re
import uuid
from typing import Optional


def sha256_hex(text: str | bytes) -> str:
    if isinstance(text, str):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        return hashlib.sha256(text).hexdigest()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def short_hash(*parts: str) -> str:
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


# Namespaces for deterministic UUIDv5
CHAT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "namespace.chat")
DASHABORD_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "namespace.dashboard")

# -----------------------------------------------------
# 1. KNOWLEDGE BASE
# -----------------------------------------------------


def generate_kb_id() -> str:
    return f"kb_{uuid.uuid4()}"


# -----------------------------------------------------
# 2. DOCUMENT
# -----------------------------------------------------


def generate_document_id() -> str:
    """Generate UUID4-based document ID (instead of hash-based)."""
    return f"doc_{uuid.uuid4()}"


def generate_content_id() -> str:
    """Generate UUID4-based content ID for deduplication with collision tracking."""
    return f"content_{uuid.uuid4()}"


# -----------------------------------------------------
# 3. CHUNK
# -----------------------------------------------------


def generate_chunk_id(doc_id: str, chunk_text: str, index: Optional[int] = None) -> str:
    raw = f"{doc_id}:{chunk_text}"
    digest = sha256_hex(raw)[:12]
    if index is not None:
        return f"chunk_{index}_{digest}"
    return f"chunk_{digest}"


# -----------------------------------------------------
# 6. CHAT SESSION
# -----------------------------------------------------


def generate_chat_id() -> str:
    return f"chat_{uuid.uuid4()}"


def generate_conv_tree_id() -> str:
    return f"tree_{uuid.uuid4()}"
