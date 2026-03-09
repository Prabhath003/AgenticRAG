# src/core/_agents/_mcp_client.py
"""
MCP Client for Agent with ChromaDB chunk navigation and KB/document tools.

Provides scoped access to ChromaDB with default collection and kb_ids.
Includes tools for:
- Listing knowledge bases and documents
- Querying and navigating chunks
- Managing context and chunk relationships
"""

from typing import List, Dict, Any, Optional
import json

from ......infrastructure.storage import get_chromadb_store
from ......infrastructure.database import get_db_session
from ......log_creator import get_file_logger
from ......config import Config
from .....models.core_models import Chunk, KnowledgeBase, Document
from ......infrastructure.operation_logging import get_operation_user_id

logger = get_file_logger()


class UserKBsIndex:
    """
    MCP Client for agent with ChromaDB chunk navigation and KB management.

    Provides scoped access to ChromaDB collections with default kb_ids filtering.
    Ensures agent operations are scoped to specific knowledge bases.
    """

    def __init__(
        self,
        kb_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ):
        """
        Initialize MCP client with default scope.

        Args:
            collection_name: ChromaDB collection to use (default: CHUNKS_COLLECTION)
            kb_ids: List of KB IDs to scope access to (if None, all user's KBs)
            user_id: User ID for scoped access (default: current operation user)
        """
        self.kb_ids = kb_ids or []
        self.user_id = user_id or get_operation_user_id()
        self.collection_name = f"chunks_{self.user_id}"
        self.chroma_store = get_chromadb_store()

        logger.info(
            f"AgentMCPClient initialized with collection='{self.collection_name}', "
            f"kb_ids={self.kb_ids}"
        )

    # ============================================================================
    # Knowledge Base Operations
    # ============================================================================

    def list_knowledge_bases(self, filters: Optional[Dict[str, Any]] = None) -> str:
        """
        List all knowledge bases for the user as LLM-passable string.

        Args:
            filters: Optional MongoDB-style filters (e.g., {'title': 'My KB'})

        Returns:
            Formatted string with kb_ids, titles, and descriptions
        """
        try:
            with get_db_session() as db:
                query = {"user_id": self.user_id}
                if filters:
                    query.update(filters)

                kbs = db[Config.KNOWLEDGE_BASES_COLLECTION].find(query).to_list()

            if not kbs:
                return "No knowledge bases found."

            lines = ["Available Knowledge Bases:"]
            for kb_entry in kbs:
                kb = KnowledgeBase(**kb_entry)
                title = kb.title or "Untitled"
                desc = kb.description or "No description"
                lines.append(f"- KB ID: {kb.kb_id}")
                lines.append(f"  Title: {title}")
                lines.append(f"  Description: {desc}")

            result = "\n".join(lines)
            logger.info(f"Listed {len(kbs)} knowledge bases")
            return result

        except Exception as e:
            logger.error(f"Failed to list knowledge bases: {e}", exc_info=True)
            return f"Error listing knowledge bases: {str(e)}"

    # ============================================================================
    # Document Operations
    # ============================================================================

    def list_documents_in_kb(self, kb_id: str) -> str:
        """
        List all documents in a knowledge base as LLM-passable string.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Formatted string with doc_ids and doc_names
        """
        try:
            # Get KB to access doc_ids
            with get_db_session() as db:
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {"_id": kb_id, "user_id": self.user_id}
                )

            if not kb_entry:
                logger.warning(f"KB {kb_id} not found")
                return f"Knowledge base '{kb_id}' not found."

            kb = KnowledgeBase(**kb_entry)

            # Get document details
            with get_db_session() as db:
                docs = db[Config.DOCUMENTS_COLLECTION].find({"_id": {"$in": kb.doc_ids}}).to_list()

            if not docs:
                return f"No documents found in KB '{kb_id}'."

            lines = [f"Documents in KB '{kb_id}':"]
            for doc_entry in docs:
                doc = Document(**doc_entry)
                source = doc.source or "upload"
                lines.append(f"- Doc ID: {doc.doc_id}")
                lines.append(f"  File Name: {doc.doc_name}")
                lines.append(f"  Source: {source}")

            result = "\n".join(lines)
            logger.info(f"Listed {len(docs)} documents in KB {kb_id}")
            return result

        except Exception as e:
            logger.error(f"Failed to list documents in KB {kb_id}: {e}")
            return f"Error listing documents: {str(e)}"

    def _get_document_info(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Get document information (name and source) by doc_id.

        Args:
            doc_id: Document ID

        Returns:
            Dict with doc_name and source, or None if not found
        """
        try:
            with get_db_session() as db:
                doc_entry = db[Config.DOCUMENTS_COLLECTION].find_one({"_id": doc_id})

            if not doc_entry:
                return None

            doc = Document(**doc_entry)
            return {"doc_name": doc.doc_name, "source": doc.source or "upload"}
        except Exception as e:
            logger.debug(f"Failed to get document info for {doc_id}: {e}")
            return None

    def _count_chunks_for_doc(self, doc_id: str) -> int:
        """Get chunk count for a document."""
        try:
            chunks = self.chroma_store.get_document_chunks_in_order(self.collection_name, doc_id)
            return len(chunks)
        except Exception:
            return 0

    # ============================================================================
    # Chunk Query Operations
    # ============================================================================

    def query_chunks(
        self,
        query_text: str,
        kb_ids: Optional[List[str]] = None,
        doc_ids: Optional[List[str]] = None,
        n_results: int = 10,
    ) -> str:
        """
        Search chunks using vector similarity as LLM-passable string.

        Args:
            query_text: Search query
            kb_ids: KB IDs to search in (defaults to client's kb_ids)
            doc_ids: Optional document IDs to limit search
            n_results: Number of results to return

        Returns:
            Formatted string with matching chunks and full content
        """
        try:
            # Use client's kb_ids if not specified
            search_kb_ids = kb_ids or self.kb_ids or []

            results = self.chroma_store.query(
                self.collection_name,
                [query_text],
                n_results=n_results,
                kb_ids=search_kb_ids if search_kb_ids else None,
                doc_ids=doc_ids,
            )
            chunks = [c for c, _ in results]

            if not chunks:
                return f"No chunks found matching query: '{query_text}'"

            lines = [f"Search Results for: '{query_text}'", f"Found {len(chunks)} chunks\n"]
            for idx, chunk in enumerate(chunks, 1):
                chunk_dict = self._chunk_to_dict(chunk)
                lines.append(f"--- Result {idx} ---")
                lines.append(f"Chunk ID: {chunk_dict['chunk_id']}")
                lines.append(f"Document ID: {chunk_dict['doc_id']}")
                lines.append("")
                lines.append(json.dumps(chunk_dict["content"], indent=1))
                lines.append("")

            result = "\n".join(lines)
            logger.info(
                f"Found {len(chunks)} chunks matching query "
                f"(kb_ids={search_kb_ids}, doc_ids={doc_ids})"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to query chunks: {e}")
            return f"Error during search: {str(e)}"

    def get_chunk_by_id(self, chunk_id: str) -> str:
        """
        Get a specific chunk by ID as LLM-passable string.

        Args:
            chunk_id: Chunk ID

        Returns:
            Formatted string with full chunk content and metadata
        """
        try:
            chunk = self.chroma_store.get_chunk_by_id(self.collection_name, chunk_id)

            if not chunk:
                logger.warning(f"Chunk {chunk_id} not found")
                return f"Chunk '{chunk_id}' not found."

            # Verify chunk is in scope
            if self.kb_ids and chunk.metadata.get("kb_id") not in self.kb_ids:
                logger.warning(f"Chunk {chunk_id} not in scope for KBs {self.kb_ids}")
                return f"Chunk '{chunk_id}' not in scope."

            chunk_dict = self._chunk_to_dict(chunk)
            lines = [f"Chunk ID: {chunk_dict['chunk_id']}"]
            lines.append(f"Document ID: {chunk_dict['doc_id']}")
            lines.append("")
            lines.append("--- CONTENT ---")
            lines.append(json.dumps(chunk_dict["content"], indent=1))
            lines.append("--- END ---")

            result = "\n".join(lines)
            return result

        except Exception as e:
            logger.error(f"Failed to get chunk {chunk_id}: {e}")
            return f"Error retrieving chunk: {str(e)}"

    # ============================================================================
    # Chunk Navigation Operations
    # ============================================================================

    def get_chunk_context(
        self,
        chunk_id: str,
        context_size: int = 1,
    ) -> str:
        """
        Get a chunk with surrounding context as LLM-passable string.

        Args:
            chunk_id: Chunk ID
            context_size: Number of chunks before and after to include

        Returns:
            Formatted string with current, before, and after chunks
        """
        try:
            neighbors = self.chroma_store.get_chunk_neighbors(
                self.collection_name,
                chunk_id,
                window_size=context_size,
            )

            if not neighbors:
                logger.warning(f"Chunk {chunk_id} not found")
                return f"Chunk '{chunk_id}' not found."

            # Find the current chunk in neighbors (middle one if available)
            current_chunk = next((c for c in neighbors if c.chunk_id == chunk_id), neighbors[0])
            current_index = neighbors.index(current_chunk)

            lines: List[str] = []

            # Before chunks
            before_chunks = neighbors[:current_index]
            if before_chunks:
                lines.append("--- BEFORE (Previous Chunks) ---")
                for chunk in before_chunks:
                    chunk_dict = self._chunk_to_dict(chunk)
                    lines.append(f"[Chunk: {chunk_dict['chunk_id']}]")
                    lines.append(json.dumps(chunk_dict["content"], indent=1))
                    lines.append("")

            # Current chunk
            current_dict = self._chunk_to_dict(current_chunk)
            lines.append("--- CURRENT CHUNK ---")
            lines.append(f"Chunk ID: {current_dict['chunk_id']}")
            lines.append(f"Document ID: {current_dict['doc_id']}")
            lines.append("")
            lines.append(json.dumps(current_dict["content"], indent=1))
            lines.append("")

            # After chunks
            after_chunks = neighbors[current_index + 1 :]
            if after_chunks:
                lines.append("--- AFTER (Next Chunks) ---")
                for chunk in after_chunks:
                    chunk_dict = self._chunk_to_dict(chunk)
                    lines.append(f"[Chunk: {chunk_dict['chunk_id']}]")
                    lines.append(json.dumps(chunk_dict["content"], indent=1))
                    lines.append("")

            result = "\n".join(lines)
            return result

        except Exception as e:
            logger.error(f"Failed to get chunk context for {chunk_id}: {e}")
            return f"Error retrieving context: {str(e)}"

    def get_previous_chunk(self, chunk_id: str) -> str:
        """
        Get the previous chunk in document sequence as LLM-passable string.

        Args:
            chunk_id: Chunk ID

        Returns:
            Formatted string with previous chunk content or message if none exists
        """
        try:
            chunk = self.chroma_store.get_previous_chunk(self.collection_name, chunk_id)

            if not chunk:
                logger.debug(f"No previous chunk for {chunk_id}")
                return f"No previous chunk found (this may be the first chunk in the document)."

            chunk_dict = self._chunk_to_dict(chunk)
            lines = [f"Previous Chunk ID: {chunk_dict['chunk_id']}"]
            lines.append(f"Document ID: {chunk_dict['doc_id']}")
            lines.append("")
            lines.append("--- CONTENT ---")
            lines.append(json.dumps(chunk_dict["content"], indent=1))
            lines.append("--- END ---")

            result = "\n".join(lines)
            return result

        except Exception as e:
            logger.error(f"Failed to get previous chunk for {chunk_id}: {e}")
            return f"Error retrieving previous chunk: {str(e)}"

    def get_next_chunk(self, chunk_id: str) -> str:
        """
        Get the next chunk in document sequence as LLM-passable string.

        Args:
            chunk_id: Chunk ID

        Returns:
            Formatted string with next chunk content or message if none exists
        """
        try:
            chunk = self.chroma_store.get_next_chunk(self.collection_name, chunk_id)

            if not chunk:
                logger.debug(f"No next chunk for {chunk_id}")
                return f"No next chunk found (this may be the last chunk in the document)."

            chunk_dict = self._chunk_to_dict(chunk)
            lines = [f"Next Chunk ID: {chunk_dict['chunk_id']}"]
            lines.append(f"Document ID: {chunk_dict['doc_id']}")
            lines.append("")
            lines.append("--- CONTENT ---")
            lines.append(json.dumps(chunk_dict["content"], indent=1))
            lines.append("--- END ---")

            result = "\n".join(lines)
            return result

        except Exception as e:
            logger.error(f"Failed to get next chunk for {chunk_id}: {e}")
            return f"Error retrieving next chunk: {str(e)}"

    def get_document_chunks(self, doc_id: str) -> str:
        """
        Get all chunks for a document as LLM-passable string.

        Args:
            doc_id: Document ID

        Returns:
            Formatted string with chunk_ids and first 100 chars of each chunk
        """
        try:
            doc_info = self._get_document_info(doc_id)
            chunks = self.chroma_store.get_document_chunks_in_order(self.collection_name, doc_id)

            if not chunks:
                return f"No chunks found for document '{doc_id}'."

            lines = [f"Chunks in Document '{doc_id}':"]
            if doc_info:
                lines.append(f"Document: {doc_info['doc_name']} (Source: {doc_info['source']})")
            lines.append("")
            for chunk in chunks:
                chunk_preview = str(chunk.content.get("text", ""))[:100]
                if len(chunk.content.get("text", "")) > 100:
                    chunk_preview += "..."
                lines.append(f"- Chunk ID: {chunk.chunk_id}")
                lines.append(f"  Preview: {chunk_preview}")

            result = "\n".join(lines)
            logger.debug(f"Got {len(chunks)} chunks for document {doc_id}")
            return result

        except Exception as e:
            logger.error(f"Failed to get document chunks for {doc_id}: {e}")
            return f"Error getting chunks: {str(e)}"

    # ============================================================================
    # Internal Dict-Returning Methods (for backward compatibility & internal use)
    # ============================================================================

    def _list_knowledge_bases_dict(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Internal method that returns structured KB data as list of dicts."""
        try:
            with get_db_session() as db:
                query = {"user_id": self.user_id}
                if filters:
                    query.update(filters)

                kbs = db[Config.KNOWLEDGE_BASES_COLLECTION].find(query).to_list()

            kb_list: List[Dict[str, Any]] = []
            for kb_entry in kbs:
                kb = KnowledgeBase(**kb_entry)
                kb_list.append(
                    {
                        "kb_id": kb.kb_id,
                        "title": kb.title or "Untitled",
                        "description": kb.description,
                        "doc_count": len(kb.doc_ids),
                        "doc_ids": kb.doc_ids,
                        "status": kb.status,
                        "created_at": kb.created_at.isoformat(),
                        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
                        "index_build_at": (
                            kb.index_build_at.isoformat() if kb.index_build_at else None
                        ),
                    }
                )

            logger.info(f"Listed {len(kb_list)} knowledge bases")
            return kb_list

        except Exception as e:
            logger.error(f"Failed to list knowledge bases: {e}", exc_info=True)
            return []

    def _list_documents_in_kb_dict(self, kb_id: str) -> List[Dict[str, Any]]:
        """Internal method that returns structured document data as list of dicts."""
        try:
            # Get KB to access doc_ids
            with get_db_session() as db:
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {"_id": kb_id, "user_id": self.user_id}
                )

            if not kb_entry:
                logger.warning(f"KB {kb_id} not found")
                return []

            kb = KnowledgeBase(**kb_entry)

            # Get document details
            doc_list: List[Dict[str, Any]] = []
            with get_db_session() as db:
                docs = db[Config.DOCUMENTS_COLLECTION].find({"_id": {"$in": kb.doc_ids}}).to_list()

            for doc_entry in docs:
                doc = Document(**doc_entry)
                # Count chunks for this document in ChromaDB
                chunk_count = self._count_chunks_for_doc(doc.doc_id)

                doc_list.append(
                    {
                        "doc_id": doc.doc_id,
                        "doc_name": doc.doc_name,
                        "content_type": doc.content_type,
                        "doc_size": doc.doc_size,
                        "chunk_count": chunk_count,
                        "uploaded_at": doc.uploaded_at.isoformat(),
                        "chunked": doc.chunked,
                        "source": doc.source or "upload",
                    }
                )

            logger.info(f"Listed {len(doc_list)} documents in KB {kb_id}")
            return doc_list

        except Exception as e:
            logger.error(f"Failed to list documents in KB {kb_id}: {e}")
            return []

    def _get_document_chunks_dict(self, doc_id: str) -> List[Dict[str, Any]]:
        """Internal method that returns structured chunk data as list of dicts."""
        try:
            chunks = self.chroma_store.get_document_chunks_in_order(self.collection_name, doc_id)

            chunk_list = [self._chunk_to_dict(c) for c in chunks]

            logger.debug(f"Got {len(chunk_list)} chunks for document {doc_id}")
            return chunk_list

        except Exception as e:
            logger.error(f"Failed to get document chunks for {doc_id}: {e}")
            return []

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _chunk_to_dict(self, chunk: Chunk) -> Dict[str, Any]:
        """Convert Chunk object to dictionary."""
        return {
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "content": chunk.content,
            "metadata": chunk.metadata,
            "created_at": chunk.created_at.isoformat(),
            "user_id": chunk.user_id,
            "chunk_order_index": chunk.content.get("chunk_order_index", 0),
            "text_preview": (
                str(chunk.content.get("text", ""))[:100] + "..."
                if chunk.content.get("text")
                else "No text preview"
            ),
        }

    def get_client_scope_info(self) -> Dict[str, Any]:
        """
        Get information about the client's current scope.

        Returns:
            Dict with collection, kb_ids, and user_id info
        """
        return {
            "collection_name": self.collection_name,
            "kb_ids": self.kb_ids,
            "kb_ids_count": len(self.kb_ids),
        }

    def validate_scope(self, kb_id: str) -> bool:
        """
        Validate if a KB ID is in scope for this client.

        Args:
            kb_id: KB ID to validate

        Returns:
            True if in scope (or client has no kb_ids constraint), False otherwise
        """
        if not self.kb_ids:
            return True
        return kb_id in self.kb_ids
