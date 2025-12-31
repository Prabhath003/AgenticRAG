# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
# All rights reserved.
#
# Developed by: 
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhathchellingi2003@gmail.com
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

# src/infrastructure/storage/json_storage.py
import os
import json
import tempfile
import shutil
import threading
import re
from typing import Any, Dict, List, Optional, Callable
from contextlib import contextmanager
from pathlib import Path

from ...log_creator import get_file_logger

logger = get_file_logger()


class JSONStorage:
    """
    Thread-safe JSON-based storage with atomic writes and file locking.
    Provides a MongoDB-like interface for storing and querying data in JSON files.
    Optimized for parallel access with per-entity and per-document granular locking.
    """

    _file_locks: Dict[str, threading.Lock] = {}
    _file_locks_lock = threading.Lock()

    def __init__(self, storage_dir: str, enable_sharding: bool = False):
        """
        Initialize JSON storage

        Args:
            storage_dir: Base directory for storing JSON files
            enable_sharding: Enable per-entity sharding for better parallel performance
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.enable_sharding = enable_sharding
        logger.info(f"Initialized JSON storage at: {self.storage_dir} (sharding={'enabled' if enable_sharding else 'disabled'})")

    @classmethod
    def _get_file_lock(cls, file_path: str) -> threading.Lock:
        """Get or create a lock for a specific file path"""
        with cls._file_locks_lock:
            if file_path not in cls._file_locks:
                cls._file_locks[file_path] = threading.Lock()
            return cls._file_locks[file_path]

    def _atomic_write_json(self, data: Any, filename: str) -> None:
        """
        Atomically write JSON data to file using temporary file and rename.
        This prevents data loss if the process is killed mid-write.
        """
        # Get the directory of the target file
        file_dir = os.path.dirname(filename) or '.'

        # Ensure directory exists
        os.makedirs(file_dir, exist_ok=True)

        # Create temp file in the same directory as target file
        # This ensures atomic rename works (must be on same filesystem)
        fd, temp_path = tempfile.mkstemp(
            suffix='.tmp',
            prefix='.tmp_' + os.path.basename(filename) + '_',
            dir=file_dir,
            text=False
        )

        try:
            # Write to temp file
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk

            # Atomic rename - this is the key operation
            # On POSIX systems, rename is atomic
            # If process is killed before this, original file is untouched
            # If process is killed during this, rename completes or doesn't happen
            if os.path.exists(filename):
                # On Windows, need to handle existing file differently
                if os.name == 'nt':
                    # Create backup first
                    backup_path = filename + '.bak'
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    shutil.copy2(filename, backup_path)
                    try:
                        os.replace(temp_path, filename)
                        # Remove backup on success
                        if os.path.exists(backup_path):
                            os.remove(backup_path)
                    except Exception as e:
                        # Restore from backup if rename fails
                        if os.path.exists(backup_path):
                            shutil.copy2(backup_path, filename)
                            os.remove(backup_path)
                        raise
                else:
                    # POSIX systems - atomic rename
                    os.replace(temp_path, filename)
            else:
                # No existing file, simple rename
                os.rename(temp_path, filename)

            logger.debug(f"Atomically wrote data to {filename}")

        except Exception as e:
            # Clean up temp file if something went wrong
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            logger.error(f"Error during atomic write to {filename}: {e}")
            raise

    def _read_json(self, filename: str) -> Any:
        """Read JSON data from file"""
        if not os.path.exists(filename):
            return None

        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from {filename}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to read {filename}: {e}")
            return None

    def _get_collection_path(self, collection_name: str, shard_key: Optional[str] = None) -> str:
        """
        Get path for a collection file with optional sharding

        Args:
            collection_name: Name of the collection
            shard_key: Optional shard key (e.g., entity_id) for parallel access
        """
        if self.enable_sharding and shard_key:
            # Create shard directory
            shard_dir = self.storage_dir / collection_name
            shard_dir.mkdir(parents=True, exist_ok=True)
            return str(shard_dir / f"{shard_key}.json")
        else:
            return str(self.storage_dir / f"{collection_name}.json")

    def _load_collection(self, collection_name: str, shard_key: Optional[str] = None) -> Dict[str, Any]:
        """Load entire collection from JSON file (with optional sharding)"""
        file_path = self._get_collection_path(collection_name, shard_key)
        lock = self._get_file_lock(file_path)

        with lock:
            data = self._read_json(file_path)
            if data is None:
                return {}
            return data

    def _save_collection(self, collection_name: str, data: Dict[str, Any], shard_key: Optional[str] = None) -> None:
        """Save entire collection to JSON file (with optional sharding)"""
        file_path = self._get_collection_path(collection_name, shard_key)
        lock = self._get_file_lock(file_path)

        with lock:
            self._atomic_write_json(data, file_path)

    def _load_all_shards(self, collection_name: str) -> Dict[str, Any]:
        """Load all shards for a collection and merge them"""
        if not self.enable_sharding:
            return self._load_collection(collection_name)

        shard_dir = self.storage_dir / collection_name
        if not shard_dir.exists():
            return {}

        merged_data = {}
        for shard_file in shard_dir.glob("*.json"):
            shard_data = self._read_json(str(shard_file))
            if shard_data:
                merged_data.update(shard_data)

        return merged_data

    def find_one(self, collection_name: str, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find a single document matching the query"""
        # Try to detect shard key from query for optimized access
        shard_key = self._extract_shard_key(query)

        if shard_key:
            # Load only the relevant shard
            collection = self._load_collection(collection_name, shard_key)
        else:
            # Load all shards
            collection = self._load_all_shards(collection_name)

        for doc_id, doc in collection.items():
            if self._matches_query(doc, query):
                return doc

        return None

    def find(self, collection_name: str, query: Dict[str, Any] = None,
             projection: Dict[str, int] = None) -> List[Dict[str, Any]]:
        """Find all documents matching the query"""
        # Try to detect shard key from query for optimized access
        shard_key = self._extract_shard_key(query) if query else None

        if shard_key:
            # Load only the relevant shard
            collection = self._load_collection(collection_name, shard_key)
        else:
            # Load all shards
            collection = self._load_all_shards(collection_name)

        results = []

        for doc_id, doc in collection.items():
            if query is None or self._matches_query(doc, query):
                if projection:
                    doc = self._apply_projection(doc, projection)
                results.append(doc)

        return results

    def _extract_shard_key(self, query: Dict[str, Any]) -> Optional[str]:
        """Extract entity_id from query for sharding"""
        if not self.enable_sharding:
            return None

        # Check for entity_id in query
        if "entity_id" in query and isinstance(query["entity_id"], str):
            return query["entity_id"]

        # Check for entity_ids array with single value
        if "entity_ids" in query:
            entity_ids = query["entity_ids"]
            if isinstance(entity_ids, str):
                return entity_ids
            elif isinstance(entity_ids, list) and len(entity_ids) == 1:
                return entity_ids[0]

        return None

    def update_one(self, collection_name: str, query: Dict[str, Any],
                   update: Dict[str, Any], upsert: bool = False) -> Dict[str, int]:
        """Update a single document with shard optimization"""
        # Extract shard key from query or update data
        shard_key = self._extract_shard_key(query)
        if not shard_key and upsert:
            # Try to extract from update data for upsert
            shard_key = self._extract_shard_key_from_update(update)

        # Get file lock for atomic load-modify-save operation
        file_path = self._get_collection_path(collection_name, shard_key)
        lock = self._get_file_lock(file_path)

        matched_count = 0
        modified_count = 0

        # Hold lock for entire load-modify-save operation to prevent race conditions
        with lock:
            # Load the appropriate shard
            collection = self._read_json(file_path)
            if collection is None:
                collection = {}

            # Find matching document
            matched_id = None
            for doc_id, doc in collection.items():
                if self._matches_query(doc, query):
                    matched_id = doc_id
                    matched_count = 1
                    break

            if matched_id:
                # Update existing document
                doc = collection[matched_id]
                if self._apply_update(doc, update):
                    modified_count = 1
                self._atomic_write_json(collection, file_path)
            elif upsert:
                # Insert new document
                new_doc = {}
                # Apply $setOnInsert if present
                if "$setOnInsert" in update:
                    new_doc.update(update["$setOnInsert"])
                # Apply $set if present
                if "$set" in update:
                    new_doc.update(update["$set"])
                # Apply $inc if present (initialize to 0 if not set)
                if "$inc" in update:
                    for field, increment_value in update["$inc"].items():
                        new_doc[field] = increment_value  # Initialize with increment value
                # Apply $addToSet if present
                if "$addToSet" in update:
                    for field, value in update["$addToSet"].items():
                        if field not in new_doc:
                            new_doc[field] = []
                        if value not in new_doc[field]:
                            new_doc[field].append(value)

                # Generate ID from query or use a default
                doc_id = query.get("_id") or query.get("doc_id") or query.get("entity_id") or str(len(collection))
                new_doc["_id"] = doc_id
                collection[doc_id] = new_doc
                self._atomic_write_json(collection, file_path)
                modified_count = 1

        return {"matched_count": matched_count, "modified_count": modified_count}

    def _extract_shard_key_from_update(self, update: Dict[str, Any]) -> Optional[str]:
        """Extract shard key from update operations"""
        if not self.enable_sharding:
            return None

        # Check $set operations
        if "$set" in update:
            if "entity_id" in update["$set"]:
                return update["$set"]["entity_id"]
            if "entity_ids" in update["$set"]:
                entity_ids = update["$set"]["entity_ids"]
                if isinstance(entity_ids, list) and len(entity_ids) > 0:
                    return entity_ids[0]

        # Check $addToSet operations
        if "$addToSet" in update:
            if "entity_ids" in update["$addToSet"]:
                value = update["$addToSet"]["entity_ids"]
                if isinstance(value, str):
                    return value

        # Check $setOnInsert operations
        if "$setOnInsert" in update:
            if "entity_id" in update["$setOnInsert"]:
                return update["$setOnInsert"]["entity_id"]
            if "entity_ids" in update["$setOnInsert"]:
                entity_ids = update["$setOnInsert"]["entity_ids"]
                if isinstance(entity_ids, list) and len(entity_ids) > 0:
                    return entity_ids[0]

        return None

    def update_many(self, collection_name: str, query: Dict[str, Any],
                    update: Dict[str, Any]) -> Dict[str, int]:
        """Update multiple documents with shard optimization"""
        shard_key = self._extract_shard_key(query)

        matched_count = 0
        modified_count = 0

        if shard_key:
            # Single shard - hold lock for entire operation
            file_path = self._get_collection_path(collection_name, shard_key)
            lock = self._get_file_lock(file_path)
            with lock:
                collection = self._read_json(file_path)
                if collection is None:
                    collection = {}

                for doc_id, doc in collection.items():
                    if self._matches_query(doc, query):
                        matched_count += 1
                        if self._apply_update(doc, update):
                            modified_count += 1

                if modified_count > 0:
                    self._atomic_write_json(collection, file_path)
        else:
            # Multiple shards - need to handle each shard's lock
            collection = self._load_all_shards(collection_name)

            for doc_id, doc in collection.items():
                if self._matches_query(doc, query):
                    matched_count += 1
                    if self._apply_update(doc, update):
                        modified_count += 1

            if modified_count > 0:
                self._save_sharded_collection(collection_name, collection)

        return {"matched_count": matched_count, "modified_count": modified_count}

    def delete_one(self, collection_name: str, query: Dict[str, Any]) -> Dict[str, int]:
        """Delete a single document with shard optimization"""
        shard_key = self._extract_shard_key(query)

        deleted_count = 0

        if shard_key:
            # Single shard - hold lock for entire operation
            file_path = self._get_collection_path(collection_name, shard_key)
            lock = self._get_file_lock(file_path)
            with lock:
                collection = self._read_json(file_path)
                if collection is None:
                    collection = {}

                for doc_id, doc in list(collection.items()):
                    if self._matches_query(doc, query):
                        del collection[doc_id]
                        deleted_count = 1
                        self._atomic_write_json(collection, file_path)
                        break
        else:
            # Multiple shards - load all and save back
            collection = self._load_all_shards(collection_name)

            for doc_id, doc in list(collection.items()):
                if self._matches_query(doc, query):
                    del collection[doc_id]
                    deleted_count = 1
                    self._save_sharded_collection(collection_name, collection)
                    break

        return {"deleted_count": deleted_count}

    def delete_many(self, collection_name: str, query: Dict[str, Any]) -> Dict[str, int]:
        """Delete multiple documents with shard optimization"""
        shard_key = self._extract_shard_key(query)

        deleted_count = 0

        if shard_key:
            # Single shard - hold lock for entire operation
            file_path = self._get_collection_path(collection_name, shard_key)
            lock = self._get_file_lock(file_path)
            with lock:
                collection = self._read_json(file_path)
                if collection is None:
                    collection = {}

                for doc_id, doc in list(collection.items()):
                    if self._matches_query(doc, query):
                        del collection[doc_id]
                        deleted_count += 1

                if deleted_count > 0:
                    self._atomic_write_json(collection, file_path)
        else:
            # Multiple shards - load all and save back
            collection = self._load_all_shards(collection_name)

            for doc_id, doc in list(collection.items()):
                if self._matches_query(doc, query):
                    del collection[doc_id]
                    deleted_count += 1

            if deleted_count > 0:
                self._save_sharded_collection(collection_name, collection)

        return {"deleted_count": deleted_count}

    def _save_sharded_collection(self, collection_name: str, collection: Dict[str, Any]) -> None:
        """Save collection data back to appropriate shards based on entity_ids"""
        if not self.enable_sharding:
            self._save_collection(collection_name, collection)
            return

        # Group documents by entity_id
        shards: Dict[str, Dict[str, Any]] = {}

        for doc_id, doc in collection.items():
            # Determine which shard this document belongs to
            doc_shard_key = None
            if "entity_id" in doc:
                doc_shard_key = doc["entity_id"]
            elif "entity_ids" in doc and isinstance(doc["entity_ids"], list) and len(doc["entity_ids"]) > 0:
                doc_shard_key = doc["entity_ids"][0]

            if doc_shard_key:
                if doc_shard_key not in shards:
                    shards[doc_shard_key] = {}
                shards[doc_shard_key][doc_id] = doc

        # Save each shard
        for shard_key, shard_data in shards.items():
            self._save_collection(collection_name, shard_data, shard_key)

    def aggregate(self, collection_name: str, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Basic aggregation support"""
        collection = self._load_collection(collection_name)
        docs = list(collection.values())

        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if self._matches_query(d, stage["$match"])]
            elif "$group" in stage:
                docs = self._group_stage(docs, stage["$group"])

        return docs

    def _matches_query(self, doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        """Check if document matches query"""
        for key, value in query.items():
            if key.startswith("$"):
                # Handle special operators
                if key == "$or":
                    if not any(self._matches_query(doc, q) for q in value):
                        return False
                elif key == "$and":
                    if not all(self._matches_query(doc, q) for q in value):
                        return False
            else:
                # Handle nested fields with dot notation
                doc_value = self._get_nested_value(doc, key)

                if isinstance(value, dict):
                    # Handle query operators
                    for op, op_value in value.items():
                        if op == "$exists":
                            exists = doc_value is not None
                            if exists != op_value:
                                return False
                        elif op == "$ne":
                            if doc_value == op_value:
                                return False
                        elif op == "$gt":
                            if doc_value is None or doc_value <= op_value:
                                return False
                        elif op == "$gte":
                            if doc_value is None or doc_value < op_value:
                                return False
                        elif op == "$lt":
                            if doc_value is None or doc_value >= op_value:
                                return False
                        elif op == "$lte":
                            if doc_value is None or doc_value > op_value:
                                return False
                        elif op == "$in":
                            if doc_value not in op_value:
                                return False
                        elif op == "$regex":
                            # Handle regex matching
                            if doc_value is None or not isinstance(doc_value, str):
                                return False
                            if not re.search(op_value, doc_value):
                                return False
                        elif op == "$not":
                            # Handle negation of sub-query
                            if not isinstance(op_value, dict):
                                return False
                            # $not negates the sub-query operators
                            for not_op, not_op_value in op_value.items():
                                if not_op == "$regex":
                                    if doc_value is not None and isinstance(doc_value, str):
                                        if re.search(not_op_value, doc_value):
                                            return False
                                elif not_op == "$eq":
                                    if doc_value == not_op_value:
                                        return False
                                elif not_op == "$in":
                                    if doc_value in not_op_value:
                                        return False
                else:
                    # Direct value comparison
                    if isinstance(doc_value, list):
                        # For array fields, check if value is in array
                        if value not in doc_value:
                            return False
                    else:
                        if doc_value != value:
                            return False

        return True

    def _get_nested_value(self, doc: Dict[str, Any], key: str) -> Any:
        """Get value from nested document using dot notation"""
        keys = key.split('.')
        value = doc

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return None

            if value is None:
                return None

        return value

    def _apply_update(self, doc: Dict[str, Any], update: Dict[str, Any]) -> bool:
        """Apply update operators to document. Returns True if document was modified."""
        modified = False

        if "$set" in update:
            for key, value in update["$set"].items():
                self._set_nested_value(doc, key, value)
                modified = True

        if "$unset" in update:
            for key in update["$unset"].keys():
                if self._unset_nested_value(doc, key):
                    modified = True

        if "$inc" in update:
            for key, increment_value in update["$inc"].items():
                current_value = self._get_nested_value(doc, key)
                # Initialize to 0 if field doesn't exist or is not a number
                if current_value is None:
                    current_value = 0
                elif not isinstance(current_value, (int, float)):
                    current_value = 0
                # Increment the value
                new_value = current_value + increment_value
                self._set_nested_value(doc, key, new_value)
                modified = True

        if "$addToSet" in update:
            for key, value in update["$addToSet"].items():
                if key not in doc:
                    doc[key] = []
                if not isinstance(doc[key], list):
                    doc[key] = [doc[key]]
                if value not in doc[key]:
                    doc[key].append(value)
                    modified = True

        if "$setOnInsert" in update:
            # Only applies during upsert, handled in update_one
            pass

        return modified

    def _set_nested_value(self, doc: Dict[str, Any], key: str, value: Any) -> None:
        """Set value in nested document using dot notation"""
        keys = key.split('.')
        current = doc

        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value

    def _unset_nested_value(self, doc: Dict[str, Any], key: str) -> bool:
        """Unset value in nested document using dot notation. Returns True if value was deleted."""
        keys = key.split('.')
        current = doc

        for k in keys[:-1]:
            if k not in current:
                return False
            current = current[k]

        if keys[-1] in current:
            del current[keys[-1]]
            return True
        return False

    def _apply_projection(self, doc: Dict[str, Any], projection: Dict[str, int]) -> Dict[str, Any]:
        """Apply projection to document"""
        if not projection:
            return doc

        result = {}
        for key, include in projection.items():
            if include:
                if key in doc:
                    result[key] = doc[key]

        return result

    def _group_stage(self, docs: List[Dict[str, Any]], group_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply $group aggregation stage"""
        groups: Dict[Any, Dict[str, Any]] = {}

        group_key = group_spec.get("_id")

        for doc in docs:
            # Get group key value
            if isinstance(group_key, str) and group_key.startswith("$"):
                key_value = self._get_nested_value(doc, group_key[1:])
            else:
                key_value = group_key

            if key_value not in groups:
                groups[key_value] = {"_id": key_value}

            # Apply accumulator operations
            for field, op in group_spec.items():
                if field == "_id":
                    continue

                if isinstance(op, dict):
                    for op_name, op_field in op.items():
                        if op_name == "$sum":
                            if field not in groups[key_value]:
                                groups[key_value][field] = 0
                            groups[key_value][field] += op_field if isinstance(op_field, int) else 1
                        elif op_name == "$push":
                            if field not in groups[key_value]:
                                groups[key_value][field] = []

                            if isinstance(op_field, dict):
                                # Push a document
                                push_doc = {}
                                for k, v in op_field.items():
                                    if isinstance(v, str) and v.startswith("$"):
                                        push_doc[k] = self._get_nested_value(doc, v[1:])
                                    else:
                                        push_doc[k] = v
                                groups[key_value][field].append(push_doc)
                            elif isinstance(op_field, str) and op_field.startswith("$"):
                                # Push a field value
                                groups[key_value][field].append(self._get_nested_value(doc, op_field[1:]))

        return list(groups.values())


class JSONStorageSession:
    """Context manager for JSON storage operations"""

    def __init__(self, storage: JSONStorage):
        self.storage = storage

    def __enter__(self):
        return self  # Return self to allow dictionary-like access

    def __exit__(self, exc_type, exc_val, exc_tb):
        # No cleanup needed for JSON storage
        pass

    def __getitem__(self, collection_name: str) -> 'JSONCollection':
        """Allow dictionary-like access to collections"""
        return JSONCollection(self.storage, collection_name)


class JSONCollection:
    """MongoDB-like collection interface for JSON storage"""

    def __init__(self, storage: JSONStorage, collection_name: str):
        self.storage = storage
        self.collection_name = collection_name

    def find_one(self, query: Dict[str, Any] = None, projection: Dict[str, int] = None) -> Optional[Dict[str, Any]]:
        """Find a single document"""
        if query is None:
            query = {}
        result = self.storage.find_one(self.collection_name, query)
        if result and projection:
            result = self.storage._apply_projection(result, projection)
        return result

    def find(self, query: Dict[str, Any] = None, projection: Dict[str, int] = None):
        """Find multiple documents"""
        if query is None:
            query = {}
        return self.storage.find(self.collection_name, query, projection)

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        """Update a single document"""
        result = self.storage.update_one(self.collection_name, query, update, upsert)

        class UpdateResult:
            def __init__(self, matched_count, modified_count):
                self.matched_count = matched_count
                self.modified_count = modified_count

        return UpdateResult(result["matched_count"], result["modified_count"])

    def update_many(self, query: Dict[str, Any], update: Dict[str, Any]):
        """Update multiple documents"""
        result = self.storage.update_many(self.collection_name, query, update)

        class UpdateResult:
            def __init__(self, matched_count, modified_count):
                self.matched_count = matched_count
                self.modified_count = modified_count

        return UpdateResult(result["matched_count"], result["modified_count"])

    def delete_one(self, query: Dict[str, Any]):
        """Delete a single document"""
        result = self.storage.delete_one(self.collection_name, query)

        class DeleteResult:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count

        return DeleteResult(result["deleted_count"])

    def delete_many(self, query: Dict[str, Any]):
        """Delete multiple documents"""
        result = self.storage.delete_many(self.collection_name, query)

        class DeleteResult:
            def __init__(self, deleted_count):
                self.deleted_count = deleted_count

        return DeleteResult(result["deleted_count"])

    def aggregate(self, pipeline: List[Dict[str, Any]]):
        """Aggregate documents"""
        return self.storage.aggregate(self.collection_name, pipeline)

    def sort(self, key: str, direction: int = 1):
        """Return a cursor-like object with sort capability"""
        class SortableCursor:
            def __init__(self, collection, key, direction):
                self.collection = collection
                self.key = key
                self.direction = direction
                self._query = {}

            def find(self, query: Dict[str, Any] = None):
                self._query = query or {}
                return self

            def __iter__(self):
                results = self.collection.storage.find(self.collection.collection_name, self._query)
                # Simple sorting by nested key
                try:
                    key_parts = self.key.split('.')
                    results.sort(
                        key=lambda x: self.collection.storage._get_nested_value(x, self.key) or '',
                        reverse=(self.direction == -1)
                    )
                except Exception as e:
                    logger.warning(f"Failed to sort results: {e}")
                return iter(results)

        return SortableCursor(self, key, direction)


# Global storage instance
_storage_instance: Optional[JSONStorage] = None


def get_storage() -> JSONStorage:
    """Get or create global JSON storage instance"""
    global _storage_instance

    if _storage_instance is None:
        from ...config import Config
        storage_dir = os.path.join(Config.DATA_DIR, "storage")
        _storage_instance = JSONStorage(storage_dir, enable_sharding=False)

    return _storage_instance


@contextmanager
def get_storage_session():
    """Get a storage session (MongoDB-like context manager)"""
    storage = get_storage()
    session = JSONStorageSession(storage)
    yield session
