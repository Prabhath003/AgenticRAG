# src/core/agents/data_models/message_storage.py
"""
Simple Message Storage with Linked List Traversal

Dictionary-based storage where messages are indexed by UUID.
Linked list traversal follows parent_message_uuid references.
Flow: latest message → ... → oldest message
"""

from typing import List, Optional, Any, Tuple, Dict, Set
from collections import OrderedDict
from datetime import datetime, timezone
import json
import asyncio
from pymongo import ReplaceOne

from ....infrastructure.database import get_db_session
from ....config import Config
from ....log_creator import get_file_logger
from ....infrastructure.clients import ModelServerClient
from ...models.agent.content_models import (
    TextContent,
    ToolUseContent,
    ToolResultContent,
    TokenBudgetContent,
)
from ...models.agent.message_models import (
    DUMMY_MESSAGE_ID,
    generate_chat_message_id_random,
    Message,
)
from ...models.agent.delta_models import (
    ContentBlockStart,
    ContentBlockDelta,
    ContentBlockStop,
)

logger = get_file_logger()


class MessageStorage:
    """
    Simple message storage using dictionaries, scoped to a specific conversation.

    Structure:
        {
            "msg-uuid-1": Message(...),
            "msg-uuid-2": Message(...),
            "msg-uuid-3": Message(...),
            ...
        }

    All messages are filtered by conversation_id during initialization and DB queries.

    Traversal follows parent_message_uuid:
        latest msg-3 → msg-2 → msg-1 → DUMMY_MESSAGE_ID

    Example:
        >>> storage = MessageStorage("conversation-123")
        >>> history = storage.get_history("latest-msg-id")
        >>> for msg in history:
        ...     print(msg.text)  # Prints messages from latest to oldest
    """

    def __init__(self, tree_id: str, leaf_message_ids: List[str]):
        """
        Initialize storage for a specific conversation.

        Args:
            conversation_id: The conversation ID to scope all operations to.
                        All loaded/queried messages will be filtered by this ID.
        """
        self.tree_id = tree_id
        self.leaf_message_ids = leaf_message_ids
        self._messages: OrderedDict[str, Message] = OrderedDict()
        self._modified_uuids: Set[str] = set()  # Track modified messages
        self._loaded = False

    def __del__(self):
        """
        Destructor: automatically push all messages to database when object is destroyed.

        Ensures that any changes made to messages in memory are persisted to the database
        before the storage object is garbage collected.
        """
        try:
            if self._messages:
                logger.info(
                    f"MessageStorage destructor: pushing {len(self._messages)} messages to database"
                )
                # Try to run async push_to_db if event loop exists, otherwise use sync wrapper
                try:
                    _ = asyncio.get_running_loop()
                    # If we're in async context, schedule the coroutine
                    asyncio.create_task(self.push_to_db())
                except RuntimeError:
                    # No running event loop, use sync wrapper
                    self._push_to_db_sync()
        except Exception as e:
            logger.error(
                f"Error in MessageStorage destructor during push_to_db: {e}",
                exc_info=True,
            )

    def _push_to_db_sync(self) -> Tuple[int, int]:
        """
        Synchronous wrapper for push_to_db (for use in destructor and sync contexts).

        Returns:
            Tuple of (successful_count, failed_count)
        """
        return self._execute_batch_write()

    def load_from_db(self, limit: int = 1000, force_reload: bool = False) -> None:
        """
        Load all messages for this conversation from database into dictionary cache.

        Filters messages by the conversation_id specified during initialization.
        Messages are sorted by MongoDB using created_at timestamp and stored in OrderedDict
        which maintains the insertion order.

        Args:
            limit: Maximum number of messages to load
            force_reload: If True, reload all messages even if already loaded (useful after pruning)
        """
        if self._loaded and not force_reload:
            return

        try:
            with get_db_session() as db:
                messages_collection = db[Config.MESSAGES]

                # Query only messages for this conversation, sorted by MongoDB using created_at
                query = {"tree_id": self.tree_id}
                docs = messages_collection.find(query).sort("created_at", 1).limit(limit).to_list()

                # Convert docs to Message objects and add to OrderedDict (maintains MongoDB sort order)
                for doc in docs:
                    message = Message(**doc)
                    self._messages[message.uuid] = message

            self._loaded = True
            logger.info(
                f"Loaded {len(self._messages)} messages for conversation {self.tree_id} (sorted by MongoDB created_at)"
            )

            self._prune_unreachable_messages()

        except Exception as e:
            logger.error(
                f"Error loading messages for conversation {self.tree_id}: {e}",
                exc_info=True,
            )
            self._loaded = True

    def _prune_unreachable_messages(self) -> int:
        """
        Remove all messages not reachable from leaf_message_ids using DFS.

        Performs depth-first search starting from leaf messages, traversing backwards
        through parent_message_uuid chain. Removes all messages that are not visited
        during this traversal.

        Returns:
            Number of messages removed
        """
        if not self._loaded:
            self.load_from_db()

        if not self._messages or not self.leaf_message_ids:
            return 0

        visited: Set[str] = set()

        def _dfs(message_uuid: str) -> None:
            """Recursively visit message and its ancestors."""
            if message_uuid in visited or message_uuid == DUMMY_MESSAGE_ID:
                return

            visited.add(message_uuid)

            if message_uuid in self._messages:
                message = self._messages[message_uuid]
                _dfs(message.parent_message_uuid)

        # Start DFS from all leaf messages
        for leaf_id in self.leaf_message_ids:
            if leaf_id in self._messages:
                _dfs(leaf_id)

        # Identify messages to remove
        messages_to_remove = set(self._messages.keys()) - visited

        # Remove unreachable messages from cache and track as modified
        for uuid in messages_to_remove:
            del self._messages[uuid]
            # Track as modified so they get pushed to DB if needed
            self._modified_uuids.add(uuid)

        removed_count = len(messages_to_remove)
        if removed_count > 0:
            logger.info(
                f"Pruned {removed_count} unreachable messages from tree {self.tree_id} "
                f"(kept {len(self._messages)} reachable messages)"
            )

        return removed_count

    def get_message(self, message_uuid: str) -> Optional[Message]:
        """
        Get a single message by UUID.

        Args:
            message_uuid: UUID of the message

        Returns:
            Message object if found, None otherwise
        """
        # Check cache
        if message_uuid in self._messages:
            return self._messages[message_uuid]

        # Load from DB if not cached
        if not self._loaded:
            self.load_from_db()

        if message_uuid in self._messages:
            return self._messages[message_uuid]
        if message_uuid != DUMMY_MESSAGE_ID:
            raise ValueError(
                f"Parent message UUID '{message_uuid}' does not exist in conversation. "
                f"Valid messages: {list(self._messages.keys())}"
            )

        logger.warning(f"Message {message_uuid} not found in tree {self.tree_id}")
        return None

    def get_history(self, leaf_uuid: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get conversation history from latest to oldest.

        Traverses backwards through parent_message_uuid chain.

        Args:
            leaf_uuid: UUID of the latest message
            limit: Maximum messages to retrieve

        Returns:
            List of messages ordered: latest → oldest

        Example:
            >>> storage = MessageStorage()
            >>> history = storage.get_history("msg-latest-id")
            >>> for msg in history:  # Iterates from latest to oldest
            ...     print(msg.uuid, msg.text)
        """
        messages: List[Dict[str, Any]] = []
        current_uuid = leaf_uuid

        # Traverse from latest backwards to oldest
        while current_uuid != DUMMY_MESSAGE_ID and limit > 0:
            message = self.get_message(current_uuid)

            if not message or message.compacted:
                logger.warning(f"Message {current_uuid} not found in history")
                break
            message_contents = message.model_dump(for_model=True)["content"]
            message_contents.reverse()
            messages.extend(message_contents)
            current_uuid = message.parent_message_uuid
            limit -= 1

        messages.reverse()

        return messages  # Order: oldest first → latest last

    def add_message(self, message: Message) -> bool:
        """
        Add a single message to storage and database.

        Args:
            message: Message object to add

        Returns:
            True if successful, False otherwise
        """
        try:
            # Set index based on parent message
            prev_message = self.get_message(message.parent_message_uuid)
            message.index = (prev_message.index + 1) if prev_message else 0

            # Add to cache and track as modified
            self._messages[message.uuid] = message
            self._modified_uuids.add(message.uuid)

            # Update leaf tracking: parent is no longer a leaf, new message is now a leaf
            if message.parent_message_uuid in self.leaf_message_ids:
                self.leaf_message_ids.remove(message.parent_message_uuid)
            if message.uuid not in self.leaf_message_ids:
                self.leaf_message_ids.append(message.uuid)

            # Persist to database
            with get_db_session() as db:
                message_dict = message.model_dump()
                message_dict["_id"] = message_dict.pop("uuid")
                db[Config.MESSAGES].insert_one(message_dict)

            logger.info(f"Added message {message.uuid} to tree {self.tree_id}")
            return True

        except Exception as e:
            logger.error(f"Error adding message {message.uuid}: {e}", exc_info=True)
            return False

    def add_content_block_to_message(
        self,
        message_id: str,
        content_block: ContentBlockStart | ContentBlockDelta | ContentBlockStop,
    ) -> bool:
        """
        Add or update a content block in an existing message during streaming.

        Handles three types of content blocks:
        - ContentBlockStart: Initiates a new content block (thinking, text, tool_use, etc.)
        - ContentBlockDelta: Appends incremental data to the current content block
        - ContentBlockStop: Marks the end of the current content block

        Args:
            message_id: UUID of the message to add content to
            content_block: ContentBlockStart, ContentBlockDelta, or ContentBlockStop

        Returns:
            True if successful, False otherwise
        """
        try:
            block_type = type(content_block).__name__

            # Get the message from cache or database
            message = self.get_message(message_id)

            if not message:
                logger.warning(f"Message {message_id} not found in tree {self.tree_id}")
                return False

            success = message.update_content(content_block)

            if success:
                self._modified_uuids.add(message_id)
            else:
                logger.warning(f"Failed to update message {message_id} with {block_type}")

            return success

        except Exception as e:
            logger.error(
                f"Error adding content block to message {message_id}: {e}",
                exc_info=True,
            )
            return False

    def get_all_messages(
        self, offset: int = 0, limit: Optional[int] = None
    ) -> OrderedDict[str, Message]:
        """
        Get paginated messages with newest-first pagination.

        Page 0 contains the newest messages. Internally, messages are stored in oldest→newest order
        (from MongoDB sort), but pagination presents them newest→oldest.

        Args:
            offset: Number of newest messages to skip (default 0 = start from newest)
            limit: Maximum messages to return (default None = all remaining messages)

        Returns:
            OrderedDict of messages ordered newest→oldest

        Example:
            >>> storage = MessageStorage("conv-123")
            >>> # Get first 10 newest messages
            >>> page0 = storage.get_all_messages(offset=0, limit=10)
            >>> # Get next 10 messages (skip first 10)
            >>> page1 = storage.get_all_messages(offset=10, limit=10)
        """
        if not self._loaded:
            self.load_from_db()

        # Convert to list and reverse to get newest first
        # (OrderedDict stores oldest→newest from MongoDB sort)
        all_items = list(reversed(list(self._messages.items())))

        # Handle offset out of range
        if offset >= len(all_items):
            return OrderedDict()

        # Calculate end index
        end_index = len(all_items) if limit is None else min(offset + limit, len(all_items))
        paginated_items = all_items[offset:end_index]

        # Return as OrderedDict maintaining the newest-first order
        result: OrderedDict[str, Message] = OrderedDict()
        for uuid, message in reversed(paginated_items):
            result[uuid] = message

        return result

    async def push_to_db(self) -> Tuple[int, int]:
        """
        Asynchronously push messages to the database using batch operations.

        Optimization strategy:
        - For normal conversations: syncs only modified messages (tracked in _modified_uuids)
        - For branched (local-only) conversations on first persistence: ensures all new messages
          are persisted along with conversation metadata to prevent data loss

        Uses bulk_write for efficient batch operations instead of sequential calls.

        Returns:
            Tuple of (successful_count, failed_count)

        Example:
            >>> storage = MessageStorage("conv-123")
            >>> # ... modify messages in storage._messages ...
            >>> successful, failed = await storage.push_to_db()
            >>> print(f"Synced {successful} messages, {failed} failed")
        """
        # Skip if no modifications
        if not self._modified_uuids:
            logger.debug("No modified messages to push to database")
            return 0, 0

        successful = 0
        failed = 0

        try:
            # Run blocking database operation in thread pool
            successful, failed = await asyncio.to_thread(self._execute_batch_write)

        except Exception as e:
            logger.error(
                f"Error in push_to_db for tree {self.tree_id}: {e}",
                exc_info=True,
            )
            failed = len(self._modified_uuids)

        return successful, failed

    def _execute_batch_write(self) -> Tuple[int, int]:
        """
        Execute batch write operation (blocking, runs in thread pool).

        Optimization for branched conversations: If a modified message's parent doesn't exist
        in the database (and isn't being synced in this batch), adds the entire ancestor chain
        to prevent orphaned messages if the original conversation is deleted.

        This is called from push_to_db() using asyncio.to_thread().

        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0

        try:
            with get_db_session() as db:
                messages_collection = db[Config.MESSAGES]

                # Build batch operations for modified messages
                # For branched conversations: if parent doesn't exist and isn't being synced,
                # add ancestor chain to ensure orphaned messages are persisted
                operations: List[ReplaceOne[Dict[str, Any]]] = []
                uuids_to_sync = set(self._modified_uuids)

                # Check if parent messages exist for modified messages not in current sync batch
                for uuid in self._modified_uuids:
                    if uuid not in self._messages:
                        continue

                    message = self._messages[uuid]
                    if (
                        message.parent_message_uuid
                        and message.parent_message_uuid != DUMMY_MESSAGE_ID
                        and message.parent_message_uuid not in self._modified_uuids
                    ):
                        # Parent not in this sync batch - check if it exists in DB
                        parent_exists = messages_collection.find_one(
                            {"_id": message.parent_message_uuid, "tree_id": self.tree_id}
                        )
                        if not parent_exists:
                            # Parent missing - add all ancestors to sync list
                            current_uuid: Optional[str] = message.parent_message_uuid
                            while current_uuid and current_uuid in self._messages:
                                uuids_to_sync.add(current_uuid)
                                current_msg = self._messages[current_uuid]
                                current_uuid = (
                                    current_msg.parent_message_uuid
                                    if current_msg.parent_message_uuid != DUMMY_MESSAGE_ID
                                    else None
                                )

                # Build operations for all messages to sync
                for uuid in uuids_to_sync:
                    if uuid not in self._messages:
                        continue

                    message = self._messages[uuid]
                    message_dict = message.model_dump(by_alias=True, exclude_none=True)

                    # Use ReplaceOne for upsert (update if exists, insert if new)
                    operations.append(
                        ReplaceOne(
                            {"_id": uuid, "tree_id": self.tree_id},
                            message_dict,
                            upsert=True,
                        )
                    )

                # Execute batch operations
                if operations:
                    extra_synced = len(uuids_to_sync) - len(self._modified_uuids)
                    if extra_synced > 0:
                        logger.debug(
                            f"Executing batch write for {len(self._modified_uuids)} modified + "
                            f"{extra_synced} ancestor messages (orphaned from branched conversation)"
                        )
                    else:
                        logger.debug(
                            f"Executing batch write for {len(operations)} modified messages"
                        )
                    result = messages_collection.bulk_write(operations, ordered=False)
                    successful = result.upserted_count + result.modified_count
                    failed = len(operations) - successful
                    logger.info(
                        f"Batch synced {successful} messages to database ({result.upserted_count} inserted, "
                        f"{result.modified_count} updated), {failed} failed"
                    )

                    # Clear modified tracking after successful push
                    self._modified_uuids.clear()

        except Exception as e:
            logger.error(
                f"Error in _execute_batch_write for tree {self.tree_id}: {e}",
                exc_info=True,
            )
            failed = len(self._modified_uuids)

        return successful, failed

    async def compact_history(self, message_id: str) -> Optional[str]:
        """
        Compact message history by creating a detailed summary message using an LLM.

        Traverses backwards from message_id to the beginning, sends all messages in that chain
        to an LLM for intelligent summarization. Marks only the input message_id as compacted,
        which acts as a blocker to prevent future history traversals from going deeper into
        that chain.

        The summary message is added as a new message with parent_message_uuid pointing to the
        input message_id. This allows future messages to reference the summary instead of
        traversing the full history.

        Args:
            message_id: UUID of the message to compact (history will be summarized from this point backwards)

        Returns:
            UUID of the new summary message if successful, None otherwise

        Example:
            >>> storage = MessageStorage("conv-123")
            >>> summary_msg_id = storage.compact_history("msg-uuid")
            >>> # Now get_history will stop at msg-uuid (marked as compacted)
            >>> # and won't traverse into its history
            >>> history = storage.get_history(summary_msg_id)
        """
        try:
            # Get the message to compact
            message = self.get_message(message_id)
            if not message:
                logger.warning(f"Message {message_id} not found")
                return None

            # Get the full history starting from message_id
            history = self.get_history(message_id, limit=10000)
            if not history:
                logger.warning(f"No history found for message {message_id}")
                return None

            # Create summary using LLM
            summary_text = self._create_summary_from_history(history)

            # Create new summary message with parent_message_uuid = message_id
            summary_message_uuid = generate_chat_message_id_random()
            summary_message = Message(
                _id=summary_message_uuid,
                tree_id=self.tree_id,
                parent_message_uuid=message_id,
                sender="assistant",
                content=[TextContent(text=summary_text)],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                index=message.index + 1,
            )

            # Close any open content blocks in the message
            self._close_message_content_blocks(message)

            # Mark only the input message as compacted (acts as a blocker)
            message.compacted = True
            message.updated_at = datetime.now(timezone.utc)
            self._modified_uuids.add(message_id)

            # Add the summary message to storage
            self.add_message(summary_message)

            # Push changes to database
            await self.push_to_db()

            logger.info(
                f"Successfully compacted message {message_id}, "
                f"created summary message {summary_message_uuid}"
            )

            return summary_message_uuid

        except Exception as e:
            logger.error(f"Error compacting history for message {message_id}: {e}", exc_info=True)
            return None

    def _create_summary_from_history(self, history: List[Dict[str, Any]]) -> str:
        """
        Create a detailed summary from message history using LLM.

        Sends the conversation history to LLM for intelligent summarization,
        capturing key points, decisions, and outcomes from the conversation.

        Args:
            history: List of message content dictionaries from get_history

        Returns:
            Summarized conversation text from LLM

        Raises:
            Exception: If the LLM call fails or model server is unavailable
        """
        try:
            # Create the summarization prompt
            compacting_prompt = """You have been working on the task described above but have not yet completed it. Write a continuation summary that will allow you (or another instance of yourself) to resume work efficiently in a future context window where the conversation history will be replaced with this summary. Your summary should be structured, concise, and actionable. Include:

1. **Task Overview**
    - The user's core request and success criteria
    - Any clarifications or constraints they specified

2. **Current State**
    - What has been completed so far
    - Files created, modified, or analyzed (with paths if relevant)
    - Key outputs or artifacts produced

3. **Important Discoveries**
    - Technical constraints or requirements uncovered
    - Decisions made and their rationale
    - Errors encountered and how they were resolved
    - What approaches were tried that didn't work (and why)

4. **Next Steps**
    - Specific actions needed to complete the task
    - Any blockers or open questions to resolve
    - Priority order if multiple steps remain

5. **Context to Preserve**
    - User preferences or style requirements
    - Domain-specific details that aren't obvious
    - Any promises made to the user

Be concise but complete—err on the side of including information that would prevent duplicate work or repeated mistakes.
    Write in a way that enables immediate resumption of the task.

Wrap your summary in <summary></summary> tags."""

            # Call LLM via the model server
            client = ModelServerClient(timeout=0)
            try:
                summary = client.openai_chat_completion(
                    messages=history
                    + [
                        {"role": "user", "content": compacting_prompt},
                    ],
                    max_tokens=8192,
                    temperature=0.3,
                )
                logger.info(f"Successfully generated summary via LLM")
                return summary
            except Exception as e:
                logger.error(f"Error calling LLM for summarization: {e}")
                # Fallback to simple summary if LLM fails
                return self._create_simple_summary(history)

        except Exception as e:
            logger.error(f"Error creating summary from history: {e}", exc_info=True)
            raise

    def _create_simple_summary(self, history: List[Dict[str, Any]]) -> str:
        """
        Create a simple fallback summary when LLM is unavailable.

        Args:
            history: List of message content dictionaries

        Returns:
            Simple formatted summary
        """
        lines = ["=== CONVERSATION SUMMARY ===\n"]
        for i, message in enumerate(history):
            if message.get("role") == "tool":
                if len(message.get("content", "")) > 50:
                    lines.append(
                        f"[Message {i}] {message.get('role')}: {message.get('content', '')[:200]}|tool_call_id:{message.get('tool_call_id')}"
                    )
                else:
                    lines.append(
                        f"[Message {i}] {message.get('role')}: {message.get('content', '')}|tool_call_id:{message.get('tool_call_id')}"
                    )
            elif not message.get("content"):
                if len(json.dumps(message.get("tool_calls", {}))) > 50:
                    lines.append(
                        f"[Message {i}] {message.get('role')}: tool_calls:{json.dumps(message.get('tool_calls', {}))[:200]}"
                    )
                else:
                    lines.append(
                        f"[Message {i}] {message.get('role')}: tool_calls:{json.dumps(message.get('tool_calls'))[:200]}"
                    )
            else:
                if len(message.get("content", "")) > 50:
                    lines.append(
                        f"[Message {i}] {message.get('role')}: {message.get('content', '')[:200]}"
                    )
                else:
                    lines.append(f"[Message {i}] {message.get('role')}: {message.get('content')}")

        return "\n".join(lines)

    def _close_message_content_blocks(self, message: Message) -> None:
        """
        Close any open content blocks in a message by setting stop_timestamp and finalizing data.

        Ensures all content blocks are properly finalized before the message is
        marked as compacted. For ToolUseContent, finalizes any accumulated JSON buffer.
        Skips special content types (ToolResultContent, TokenBudgetContent) that don't
        have stop_timestamp.

        Args:
            message: Message object whose content blocks should be closed
        """
        try:
            current_time = datetime.now(timezone.utc)

            for content_block in message.content:
                if content_block is None:
                    continue

                # Skip content types that don't have stop_timestamp
                if isinstance(content_block, (ToolResultContent, TokenBudgetContent)):
                    continue

                # Handle ToolUseContent - finalize JSON buffer if present
                if isinstance(content_block, ToolUseContent):
                    try:
                        parsed_input = content_block.unsetJSONBuffer()
                        if parsed_input:
                            content_block.input = parsed_input
                            logger.debug(f"Finalized JSON buffer for tool {content_block.name}")
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"Failed to parse JSON buffer for tool {content_block.name}: {e}"
                        )

                # Close the content block if it doesn't have a stop_timestamp
                if (
                    hasattr(content_block, "stop_timestamp")
                    and content_block.stop_timestamp is None
                ):
                    content_block.stop_timestamp = current_time
                    logger.debug(
                        f"Closed content block of type {type(content_block).__name__} "
                        f"in message {message.uuid}"
                    )

            logger.info(f"Closed all open content blocks in message {message.uuid}")

        except Exception as e:
            logger.error(
                f"Error closing content blocks for message {message.uuid}: {e}",
                exc_info=True,
            )

    def delete_messages(self) -> Tuple[int, int]:
        """
        Delete only orphaned messages from cache and database.

        Only deletes messages that are:
        - Currently in self._messages (loaded in cache)
        - NOT reachable from any leaf_message_ids (not part of active tree)

        Loads the full tree separately to determine reachability without
        disturbing self._messages. Only deletes orphaned branches.

        Returns:
            Tuple of (deleted_count, failed_count)

        Example:
            >>> storage = MessageStorage("conv-123")
            >>> deleted, failed = storage.delete_messages()
            >>> print(f"Deleted {deleted} orphaned messages, {failed} failed")
        """
        if not self._messages or not self.leaf_message_ids:
            return 0, 0

        deleted_count = 0
        failed_count = 0

        # Load full tree separately into temporary dict (doesn't disturb self._messages)
        all_messages: Dict[str, Message] = {}
        try:
            with get_db_session() as db:
                messages_collection = db[Config.MESSAGES]
                query = {"tree_id": self.tree_id}
                docs = messages_collection.find(query).sort("created_at", 1).to_list()
                for doc in docs:
                    message = Message(**doc)
                    all_messages[message.uuid] = message
        except Exception as e:
            logger.error(
                f"Error loading full tree for deletion analysis: {e}",
                exc_info=True,
            )
            return 0, 1

        # Find reachable messages using DFS on full tree
        to_delete: Set[str] = set()

        def _mark_to_delete(message_uuid: str) -> None:
            """Recursively visit message and its ancestors in full tree."""
            if message_uuid in to_delete or message_uuid == DUMMY_MESSAGE_ID:
                return
            to_delete.add(message_uuid)
            if message_uuid in all_messages:
                message = all_messages[message_uuid]
                _mark_to_delete(message.parent_message_uuid)

        # Start DFS from all leaf messages
        for leaf_id in self.leaf_message_ids:
            if leaf_id in all_messages:
                _mark_to_delete(leaf_id)

        # Identify orphaned messages: in cache but not reachable from leaves
        orphaned_messages = set(self._messages.keys()) - to_delete

        # with get_db_session() as db:
        #     convs = db[Config.CONVERSATIONS_COLLECTION].find(
        #         {"tree_id": self.tree_id}
        #     )

        def _mark_common(message_uuid: str) -> None:
            """Recursively visit message and its ancestors in full tree."""
            if message_uuid == DUMMY_MESSAGE_ID:
                return
            if message_uuid in to_delete:
                to_delete.remove(message_uuid)
            if message_uuid in all_messages:
                message = all_messages[message_uuid]
                _mark_common(message.parent_message_uuid)

        for message_uuid in orphaned_messages:
            if message_uuid in all_messages:
                _mark_common(message_uuid)

        try:
            # Delete orphaned messages from database
            with get_db_session() as db:
                messages_collection = db[Config.MESSAGES]
                result = messages_collection.delete_many({"_id": {"$in": list(to_delete)}})
                deleted_count = result.deleted_count
                failed_count = len(to_delete) - deleted_count
                logger.info(
                    f"Deleted {deleted_count} orphaned messages from database for tree {self.tree_id}"
                )

        except Exception as e:
            logger.error(
                f"Error deleting orphaned messages from database for tree {self.tree_id}: {e}",
                exc_info=True,
            )
            failed_count = len(to_delete)

        try:
            # Clear in-memory cache
            messages_cleared = len(self._messages)
            self._messages.clear()
            self.leaf_message_ids = [DUMMY_MESSAGE_ID]
            self._loaded = False  # Reset the loaded flag to allow reloading if needed

            logger.info(
                f"Cleared {messages_cleared} messages from in-memory cache for tree {self.tree_id}"
            )

        except Exception as e:
            logger.error(
                f"Error clearing orphaned messages from in-memory cache for tree {self.tree_id}: {e}",
                exc_info=True,
            )
            failed_count += 1

        return deleted_count, failed_count
