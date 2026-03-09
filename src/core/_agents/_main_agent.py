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

# src/core/agents/research_agent.py
from typing import (
    List,
    Dict,
    Any,
    Optional,
    AsyncGenerator,
    Tuple,
    Union,
    Literal,
    Callable,
    Coroutine,
    Set,
)
import json
import asyncio
from openai import AsyncOpenAI, AsyncAzureOpenAI
from openai.types.chat import ChatCompletionChunk
from openai._streaming import AsyncStream
import re
from threading import Lock
from pydantic import BaseModel, ConfigDict, Field

from ._tool_manager import ToolManager
from ._agent_utils import (
    sync_get_model_pricing,
    calculate_openai_cost_sync,
    parse_citations_from_content_sync,
    count_tokens_sync,
    estimate_message_tokens_sync,
    parse_content_block_type_sync,
    strip_xml_tags_sync,
)
from ..models.agent import Style
from ..models.agent.message_models import (
    MessageStart,
    MessageDelta,
    MessageDeltaDelta,
    MessageStop,
)
from ..models.agent.delta_models import (
    ContentBlockStart,
    ContentBlockDelta,
    ContentBlockStop,
    TextContentDelta,
    InputJSONDelta,
    ThoughtContentDelta,
    ThoughtSummaryDelta,
    ToolUseBlockUpdateDelta,
    CitationDelta,
)
from ..models.agent.content_models import (
    TextContent,
    ToolUseContent,
    ThoughtContent,
    ThoughtSummary,
    ToolResultContent,
)
from ..models.agent.message_models import Message
from ...config import Config
from ...log_creator import get_file_logger
from ..models.agent.message_models import (
    generate_chat_message_id_random,
)
from ...infrastructure.operation_logging import (
    log_service,
    update_operation_metadata,
    get_operation_user_id,
)
from ..models.operation_audit import ServiceType
from ...infrastructure.database import get_db_session
from ._conversation_manager import ConversationManager
from ..models.core_models import Conversation
from ...infrastructure.utils import extract_json_from_llm_response
from ..models._request_models import (
    ConverseRequest,
    StopConverseRequest,
    ConversationRequest,
)

logger = get_file_logger()

# Constants for streaming control
THOUGHT_CHUNKS_TRIGGER_K = 50
CHUNKS_WITHOUT_BLOCK_THRESHOLD = 4


class StreamState(BaseModel):
    """Manages shared state during streaming response processing"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Content block tracking
    content_block_started: bool = False
    current_block_type: Optional[Literal["thought", "text"]] = None
    previous_block_type: Optional[Literal["thought", "text"]] = None
    is_converted_thought_block: bool = False
    current_block_index: int = 0  # Track current content block index

    # Content buffering
    content_buffer: str = ""
    pending_content: str = ""
    clean_content: str = ""
    incomplete_tag_chunks: int = 0
    chunks_without_block_type: int = 0

    # Thought tracking
    thought_chunk_count: int = 0
    accumulated_thought_text: str = ""
    summary_queue: asyncio.Queue[Dict[str, Any]] = Field(default_factory=lambda: asyncio.Queue())

    # Citation tracking
    yielded_citations: Set[str] = Field(default_factory=set)
    full_text_buffer: str = ""

    # Tool call tracking
    func_calls: List[Dict[str, Any]] = Field(default_factory=lambda: [])
    parsed_func_calls: List[Tuple[Dict[str, Any], Dict[str, Any]]] = Field(
        default_factory=lambda: []
    )
    tool_call_processed: bool = False


class MainAgent:
    def __init__(self, conversation_model: Conversation):
        """
        Initialize AgenticPlannerSystem with configurable expert access.

        Args:
            id: Unique identifier for this agent
            entity_name: Name of the entity this agent serves
            use_entity_scoped: Whether to use entity-scoped data
            available_expert_ids: List of expert IDs this agent can access.
                                If None, all experts are available.
                                Example: ["python_expert", "database_expert"]
            on_state_change_callback: Optional callback function to call when agent state changes
        """
        # Initialize OpenAI or Azure OpenAI client based on availability
        self.client: Union[AsyncOpenAI, AsyncAzureOpenAI, None] = None
        self.use_azure = False

        # Try Azure OpenAI first if all required credentials are available
        if (
            Config.AZURE_OPENAI_ENDPOINT
            and Config.AZURE_OPENAI_KEY
            and Config.AZURE_OPENAI_DEPLOYMENT
        ):
            try:
                self.client = AsyncAzureOpenAI(
                    azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
                    api_key=Config.AZURE_OPENAI_KEY,
                    api_version=Config.AZURE_OPENAI_VERSION,
                )
                self.use_azure = True
                self.model_name = Config.AZURE_OPENAI_DEPLOYMENT
                logger.info(
                    f"Initialized Planner with Azure OpenAI (deployment: {self.model_name})"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to initialize Azure OpenAI client: {e}. Falling back to OpenAI..."
                )
                self.client = None

        # Fall back to OpenAI if Azure is not available or failed
        if self.client is None and Config.OPENAI_API_KEY:
            try:
                self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
                self.use_azure = False
                self.model_name = Config.GPT_MODEL
                logger.info(f"Initialized Planner with OpenAI (model: {self.model_name})")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")
                self.client = None

        # Raise error if no client could be initialized
        if self.client is None:
            raise Exception(
                "Failed to initialize research agent. Please provide either:\n"
                "  - OPENAI_API_KEY for OpenAI, or\n"
                "  - AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT for Azure OpenAI"
            )

        self.conversation_model = conversation_model

        # Lock for protecting concurrent access to estimated_cost_usd
        self._cost_lock = Lock()

        # Message queue infrastructure for async message processing
        self._message_queue: asyncio.Queue[ConverseRequest] = asyncio.Queue()
        self._queue_processing = False
        self._stop_requested = False

        # Initialize tools with expert configuration
        self.conversation_manager = ConversationManager(
            self.conversation_model.tree_id,
            self.conversation_model.leaf_message_ids,
            self.conversation_model.current_leaf_message_id,
            conversation_id=self.conversation_model.conversation_id,
            kb_ids=self.conversation_model.kb_ids,
            user_instructions=self.conversation_model.user_instructions,
        )
        self.tool_manager = ToolManager(self.conversation_manager)

    def model_dump(
        self,
        by_alias: Optional[bool] = None,
        exclude_none: bool = False,
        mode: Literal["python", "json"] = "python",
    ) -> Dict[str, Any]:
        self.conversation_model.current_leaf_message_id = (
            self.conversation_manager.current_leaf_message_id
        )
        self.conversation_model.leaf_message_ids = self.conversation_manager.leaf_message_ids
        return self.conversation_model.model_dump(
            by_alias=by_alias, exclude_none=exclude_none, mode=mode
        )

    async def push_to_db(self):
        """Push both conversation messages and conversation metadata to database asynchronously."""
        logger.info("Starting async push_to_db: syncing conversation messages and metadata")
        await asyncio.gather(
            self.conversation_manager.push_to_db(), self._push_conversation_metadata_to_db()
        )
        logger.info("Completed async push_to_db: all changes synced to database")

    async def _push_conversation_metadata_to_db(self) -> None:
        """Push conversation metadata to database asynchronously."""
        logger.debug(f"Pushing conversation metadata for {self.conversation_model.conversation_id}")
        await asyncio.to_thread(self._sync_push_conversation_metadata_to_db)
        logger.debug("Conversation metadata synced to database")

    def _sync_push_conversation_metadata_to_db(self) -> None:
        """Synchronous wrapper for database operations (runs in thread pool)."""
        with get_db_session() as db:
            db[Config.CONVERSATIONS_COLLECTION].update_one(
                {
                    "_id": self.conversation_model.conversation_id,
                    "user_id": get_operation_user_id(),
                },
                {"$set": self.model_dump(by_alias=True, exclude_none=True)},
                upsert=True,
            )

    async def _fetch_latest_settings_and_metadata(self) -> None:
        """
        Fetch the latest settings, name, and summary from the database asynchronously.
        Updates the agent instance with the fetched values.
        """
        await asyncio.to_thread(self._sync_fetch_latest_settings_and_metadata)

    def _sync_fetch_latest_settings_and_metadata(self) -> None:
        """Synchronous wrapper for fetching from database (runs in thread pool)."""
        try:
            with get_db_session() as db:
                doc = db[Config.CONVERSATIONS_COLLECTION].find_one(
                    {
                        "_id": self.conversation_model.conversation_id,
                        "user_id": get_operation_user_id(),
                    }
                )
                if doc:
                    # Update settings if present in database
                    self.conversation_model = Conversation(**doc)
                else:
                    logger.debug(
                        f"Conversation {self.conversation_model.conversation_id} not found in database"
                    )
        except Exception as e:
            logger.warning(f"Failed to fetch latest settings from database: {e}")

    @staticmethod
    def load(**dump: Any):
        return MainAgent(Conversation(**dump))

    # =========================================================================
    # TOKEN & COST MANAGEMENT METHODS
    # =========================================================================
    # Methods for managing tokens, model pricing, cost calculations
    # Related utility functions: src/core/_agents/_agent_utils.py

    async def _get_model_pricing(self, model_name: str) -> Tuple[float, float, float]:
        """
        Get input, output, and cache token pricing for different OpenAI models asynchronously.

        Args:
            model_name: Name of the model (e.g., 'gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini')

        Returns:
            Tuple of (input_price_per_1M_tokens, output_price_per_1M_tokens, cache_read_price_per_1M_tokens)
        """
        return await asyncio.to_thread(sync_get_model_pricing, model_name)

    async def _calculate_openai_cost(
        self, input_tokens: int, output_tokens: int, cache_tokens: int = 0
    ) -> float:
        """
        Async wrapper for OpenAI cost calculation.

        Calculate OpenAI API cost based on model and token usage, including cached tokens.

        Args:
            input_tokens: Number of regular input tokens (not cached)
            output_tokens: Number of output tokens
            cache_tokens: Tokens read from cache (optional)

        Returns:
            Total cost in USD, rounded to 6 decimal places
        """
        return await asyncio.to_thread(
            calculate_openai_cost_sync,
            input_tokens,
            output_tokens,
            cache_tokens,
            self.model_name,
        )

    # =========================================================================
    # CONTENT PROCESSING METHODS (Begins)
    # =========================================================================
    # Methods for parsing citations, detecting content blocks, processing content
    # Related utility functions: src/core/_agents/_agent_utils.py

    async def _parse_citations_from_content(
        self, content: str
    ) -> tuple[str, List[str], List[Dict[str, Any]]]:
        """
        Async wrapper for citation parsing.

        Parse inline citations from content and return cleaned content + citations

        Args:
            content: Content potentially containing citations

        Returns:
            Tuple of (content_with_citations, cited_node_ids, citations_list)
        """
        return await asyncio.to_thread(parse_citations_from_content_sync, content)

    # =========================================================================
    # MESSAGE PROCESSING & RESPONSE GENERATION METHODS
    # =========================================================================
    # Main entry point and high-level response generation

    async def process_user_messages(
        self,
    ) -> AsyncGenerator[
        Union[
            MessageStart,
            MessageDelta,
            MessageStop,
            ContentBlockStart,
            ContentBlockDelta,
            ContentBlockStop,
        ],
        None,
    ]:
        """
        Process messages from the queue and yield streaming responses.

        Entry point for message processing that manages queue, batching, and response generation.

        Workflow:
        1. Wait for message(s) in queue
        2. Batch-add all user messages to conversation manager
        3. For each message in batch:
            - Create assistant response placeholder
            - Yield MessageStart
            - Generate response via _generate_response() and yield chunks
            - Yield related questions (MessageDelta)
            - Update conversation
        4. Repeat until stop requested AND queue empty
        5. Yield MessageStop and exit

        Yields:
            Stream of MessageStart → response chunks → MessageDelta → MessageStop
        """
        if self._queue_processing:
            logger.debug("Queue processor already running, skipping")
            return

        self._queue_processing = True
        logger.info("Starting message processing")
        response_uuid = None

        try:
            while not self._message_queue.empty():
                try:
                    # Collect all currently available messages (batch)
                    batch_requests: List[ConverseRequest] = []
                    while not self._message_queue.empty():
                        try:
                            request = self._message_queue.get_nowait()
                            batch_requests.append(request)
                        except asyncio.QueueEmpty:
                            break

                    batch_size = len(batch_requests)
                    logger.debug(
                        f"Batch collected: {batch_size} message(s). Remaining in queue: {self._message_queue.qsize()}"
                    )

                    personalized_styles: List[Style] = []
                    # Batch-add all user messages to conversation manager
                    for idx, request in enumerate(batch_requests):
                        try:
                            self._add_user_message_to_conversation(request)
                            if not personalized_styles:
                                personalized_styles = request.personalized_styles
                            logger.debug(f"User message {idx+1}/{batch_size} added to conversation")
                        except Exception as e:
                            logger.error(
                                f"Error adding user message {idx+1}/{batch_size}: {e}",
                                exc_info=True,
                            )

                    # Queue is empty - check if we should exit
                    if self._stop_requested:
                        logger.debug("Queue empty and stop requested, exiting")
                        if response_uuid:
                            message_delta = MessageDelta(
                                delta=MessageDeltaDelta(stop_reason="user_interruption")
                            )
                            yield message_delta
                            self.conversation_manager.update_message(response_uuid, message_delta)
                        break

                    # Process messages - generate responses and yield
                    try:
                        # Fetch latest settings before processing
                        await self._fetch_latest_settings_and_metadata()

                        user_message_uuid = self.conversation_manager.current_leaf_message_id
                        response_uuid = generate_chat_message_id_random()

                        # Create assistant response placeholder
                        self.conversation_manager.add_message(
                            Message(
                                _id=response_uuid,
                                tree_id=self.conversation_model.tree_id,
                                sender="assistant",
                                parent_message_uuid=user_message_uuid,
                            )
                        )

                        # Yield MessageStart
                        yield MessageStart(
                            message=Message(
                                _id=response_uuid,
                                tree_id=self.conversation_model.tree_id,
                                sender="assistant",
                                parent_message_uuid=user_message_uuid,
                            )
                        )

                        update_operation_metadata({"$addToSet": {"message_uuids": [response_uuid]}})

                        # Generate response and yield all chunks
                        responses_gen, _ = await self._generate_response(
                            response_uuid, personalized_styles
                        )
                        async for response in responses_gen:
                            yield response

                        # Generate related questions if no stop reason
                        response_message = self.conversation_manager.get_message(response_uuid)
                        if response_message:
                            if response_message.stop_reason == "context_limit":
                                # async for response in  self._compact_context(response_uuid):
                                #     yield response
                                pass
                            elif response_message.stop_reason == "usage_limit":
                                # Add all remaining queued messages to conversation manager
                                while not self._message_queue.empty():
                                    try:
                                        queued_request = self._message_queue.get_nowait()
                                        self._add_user_message_to_conversation(queued_request)
                                    except asyncio.QueueEmpty:
                                        break

                                break
                            else:
                                if response_message.stop_reason is None:
                                    message_delta = MessageDelta(
                                        delta=MessageDeltaDelta(stop_reason="end_turn")
                                    )
                                    yield message_delta
                                    self.conversation_manager.update_message(
                                        response_uuid, message_delta
                                    )

                                if self._message_queue.empty():
                                    related_questions = await self._generate_related_questions(
                                        num_turns=5, num_questions=3
                                    )
                                    formatted_questions = [
                                        {"id": f"{response_uuid}_q{i}", "text": question}
                                        for i, question in enumerate(related_questions)
                                    ]
                                    message_delta = MessageDelta(
                                        delta=MessageDeltaDelta(
                                            stop_reason=None, related_questions=formatted_questions
                                        )
                                    )
                                    yield message_delta
                                    self.conversation_manager.update_message(
                                        response_uuid,
                                        message_delta,
                                    )

                        logger.debug(f"Messages processed successfully")

                    except Exception as e:
                        logger.error(
                            f"Error processing messages: {e}",
                            exc_info=True,
                        )
                        if response_uuid:
                            message_delta = MessageDelta(
                                delta=MessageDeltaDelta(stop_reason="error")
                            )
                            yield message_delta
                            self.conversation_manager.update_message(response_uuid, message_delta)
                        # Continue processing other messages even if one fails
                        continue

                    logger.debug(
                        f"Batch of {batch_size} messages processed. Queue size: {self._message_queue.qsize()}"
                    )

                except asyncio.TimeoutError:
                    # Timeout is normal - check if we should exit
                    if self._stop_requested and self._message_queue.empty():
                        logger.debug("Queue empty and stop requested, exiting")
                        break
                    # Otherwise continue waiting for new items
                    logger.debug("Queue timeout - waiting for more messages...")
                    continue

                except asyncio.CancelledError:
                    logger.debug("Queue processing cancelled")
                    raise

                except Exception as e:
                    logger.error(f"Unexpected error in message processing: {e}", exc_info=True)
                    break

            # Yield final MessageStop
            yield MessageStop()
            logger.debug("All messages processed - yielding MessageStop")

        except Exception as e:
            logger.error(f"Error in message processing: {e}", exc_info=True)
            if response_uuid:
                message_delta = MessageDelta(delta=MessageDeltaDelta(stop_reason="error"))
                yield message_delta
                self.conversation_manager.update_message(response_uuid, message_delta)
            yield MessageStop()

        finally:
            self._queue_processing = False
            self._stop_requested = False
            logger.info("Message processing complete")

            # Push all changes to database
            logger.info("Pushing to DB")
            await asyncio.gather(
                self.conversation_manager.push_to_db(),
                self._summarize(),
                self._generate_conversation_name(),
            )
            logger.debug("Syncing conversation metadata")
            await self._push_conversation_metadata_to_db()
            logger.info("DB sync complete: all changes persisted")

    async def _count_tokens(self, text: str) -> int:
        """
        Async wrapper for token counting.

        Count tokens in text using tiktoken for accurate token calculation.

        Args:
            text: Text to count tokens for

        Returns:
            Token count
        """
        return await asyncio.to_thread(count_tokens_sync, text)

    async def _estimate_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        Async wrapper for message token estimation.

        Estimate total token count for a list of messages including tool_calls.

        Args:
            messages: List of message dictionaries

        Returns:
            Estimated total token count
        """
        return await asyncio.to_thread(estimate_message_tokens_sync, messages)

    async def _check_context_approaching_limit(self) -> bool:
        """
        Check if message context is approaching model's limit.

        Args:
            response_uuid: Current response UUID

        Returns:
            True if context is approaching limit, False otherwise
        """
        try:
            # Get current message history
            messages = self.conversation_manager.get_message_history(
                self.conversation_manager.current_leaf_message_id,
                settings=self.conversation_model.settings,
                personalized_styles=[],
            )
            estimated_tokens = await self._estimate_message_tokens(messages)

            # Model context limit (GPT-4/5 mini has ~128k context)
            # Use 85% as threshold to trigger compacting
            MODEL_CONTEXT_LIMIT = 128000
            COMPACT_THRESHOLD = int(MODEL_CONTEXT_LIMIT * 0.85)

            logger.info(
                f"Context check: {estimated_tokens}/{MODEL_CONTEXT_LIMIT} tokens (threshold: {COMPACT_THRESHOLD})"
            )

            return estimated_tokens > COMPACT_THRESHOLD
        except Exception as e:
            logger.error(f"Error checking context: {e}", exc_info=True)
            return False

    async def _compact_context(self) -> Optional[str]:
        """
        Compact message context.

        Args:
            response_uuid: Current response UUID

        Returns:
            New message UUID if compacting was performed, None otherwise
        """
        try:
            logger.info(f"Initiating context compacting...")
            await self.tool_manager.compact(self.conversation_manager.current_leaf_message_id)

            # After compacting, the leaf message changes, update our reference
            new_leaf_id = self.conversation_manager.current_leaf_message_id
            logger.info(f"Compacting completed. New leaf message ID: {new_leaf_id}")
            return new_leaf_id
        except Exception as e:
            logger.error(f"Error compacting context: {e}", exc_info=True)
            return None

    async def _parse_content_block_type(
        self, accumulated_content: str
    ) -> Optional[Literal["thought", "text"]]:
        """
        Async wrapper for content block type parsing.

        Parse the current position in accumulated content and determine the block type.

        Args:
            accumulated_content: All accumulated content so far

        Returns:
            "thought" if inside <thinking> tag, "text" if inside <answer> tag, else None
        """
        return await asyncio.to_thread(parse_content_block_type_sync, accumulated_content)

    async def _strip_xml_tags(self, content: str) -> str:
        """
        Async wrapper for XML tag stripping.

        Remove XML tags from content while preserving the actual text.

        Args:
            content: Content potentially containing XML tags

        Returns:
            Content with XML tags removed
        """
        return await asyncio.to_thread(strip_xml_tags_sync, content)

    # =========================================================================
    # QUEUE MANAGEMENT METHODS
    # =========================================================================
    # Methods for managing asynchronous message processing queue

    def submit_message(self, request: ConversationRequest) -> None:
        """
        Submit a message to the processing queue or request queue shutdown.

        Args:
            request: Either a ConverseRequest with a prompt to process,
                    or a StopConverseRequest to signal queue shutdown.
                    Queue will process remaining messages and exit after StopConverseRequest.
        """
        if isinstance(request, StopConverseRequest):
            # Set stop flag immediately - no queue item needed
            self._stop_requested = True
            logger.debug(
                f"Stop request received - reason: {request.reason} - queue will process remaining items and exit"
            )
        else:
            # Add a regular message to the queue
            self._message_queue.put_nowait(request)
            logger.debug(f"Message submitted to queue. Queue size: {self._message_queue.qsize()}")

    def _add_user_message_to_conversation(self, request: ConverseRequest) -> str:
        """
        Helper method to add a user message to the conversation.

        Args:
            request: The conversation request containing user prompt and attachments

        Returns:
            The UUID of the added user message
        """
        user_message_uuid = generate_chat_message_id_random()
        parent_uuid = (
            request.parent_message_uuid or self.conversation_manager.current_leaf_message_id
        )

        self.conversation_manager.add_message(
            Message(
                _id=user_message_uuid,
                tree_id=self.conversation_model.tree_id,
                sender="human",
                content=[TextContent(text=request.prompt)],
                files=self.tool_manager.upload(request.files or []),
                attachments=request.attachments or [],
                parent_message_uuid=parent_uuid,
            )
        )
        return user_message_uuid

    # =========================================================================
    # STREAMING & CONTENT BLOCK MANAGEMENT METHODS
    # =========================================================================
    # Methods for handling streaming content, block transitions, and summaries

    async def _generate_thought_summary(
        self,
        thought_text: str,
        summary_index: int,
        on_ready_callback: Callable[[int, str], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Generate a concise summary of thought text using OpenAI and call callback when ready.

        Args:
            thought_text: The accumulated thought text to summarize
            summary_index: The index to assign to this summary in the content stream
            on_ready_callback: Async callback to call when summary is ready
        """
        try:
            if not thought_text or len(thought_text.strip()) < 50:
                return

            response = await self.client.chat.completions.create(  # type: ignore
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a concise summarizer. Summarize the following thought process in 1-2 sentences, capturing the key reasoning or conclusion.",
                    },
                    {
                        "role": "user",
                        "content": f"Summarize this thought:\n\n{thought_text}",
                    },
                ],
                max_tokens=100,
                temperature=0.5,
            )

            summary_text = response.choices[0].message.content or ""
            if response.usage:
                usage_dict = response.usage.model_dump(exclude_none=True)
                if usage_dict:
                    cost_usd = await self._calculate_openai_cost(
                        usage_dict.get("prompt_tokens", 0)
                        - usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                        usage_dict.get("completion_tokens", 0),
                        usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                    )

                    if cost_usd:
                        log_service(
                            service_type=ServiceType.OPENAI,
                            estimated_cost_usd=cost_usd,
                            breakdown=usage_dict,
                            description="Thought Summary Generation",
                            metadata={"approx_content_index": summary_index},
                        )
                        with self._cost_lock:
                            self.conversation_model.estimated_cost_usd += cost_usd
            if summary_text:
                await on_ready_callback(summary_index, summary_text)
        except Exception as e:
            logger.debug(f"Failed to generate thought summary: {e}")

    async def _process_content_chunk(
        self,
        state: StreamState,
        delta_content: str,
    ) -> Tuple[str, bool]:
        """Process a content delta chunk and update state

        Returns:
            clean_content string
        """
        # Add delta to pending content (buffering)
        state.pending_content += delta_content

        # Check if we're waiting for an incomplete tag
        has_incomplete_tag = bool(re.search(r"</?[a-z]*$", state.pending_content))

        if has_incomplete_tag:
            # Incomplete tag detected, buffer it
            state.incomplete_tag_chunks += 1
            if state.incomplete_tag_chunks <= 2:
                # Wait up to 2 more chunks for the tag to complete
                return "", True
            else:
                # Waited long enough, reset and continue streaming
                state.incomplete_tag_chunks = 0
        else:
            state.incomplete_tag_chunks = 0

        # Add to content buffer for tag detection
        state.content_buffer += state.pending_content

        # Detect current block type based on XML tags
        state.current_block_type = await self._parse_content_block_type(state.content_buffer)

        # Convert thought blocks to text when extended thinking is disabled
        state.is_converted_thought_block = False
        if (
            state.current_block_type == "thought"
            and not self.conversation_model.settings.enabled_extended_thinking
        ):
            state.current_block_type = "text"
            state.is_converted_thought_block = True

        # Strip XML tags to check for clean content
        clean_content = await self._strip_xml_tags(state.pending_content)

        # If no block type detected but we have content, track chunks without block type
        if state.current_block_type is None and clean_content:
            state.chunks_without_block_type += 1
            if state.chunks_without_block_type >= CHUNKS_WITHOUT_BLOCK_THRESHOLD:
                # Default to text block type after threshold
                state.current_block_type = "text"
                state.chunks_without_block_type = 0
        elif state.current_block_type is not None:
            # Reset counter when we find a valid block type
            state.chunks_without_block_type = 0

        return clean_content, False

    async def _handle_block_transitions(
        self,
        state: StreamState,
        clean_content: str,
        response_uuid: str,
        on_summary_ready: Optional[Callable[[int, str], Coroutine[Any, Any, None]]] = None,
    ) -> AsyncGenerator[Union[ContentBlockStop, ContentBlockStart], None]:
        """Handle transitions between content blocks and yield immediately"""
        # Handle block type transition
        if state.previous_block_type != state.current_block_type:
            # Add separator newline when exiting converted thought block
            if (
                state.is_converted_thought_block
                and state.previous_block_type == "text"
                and clean_content
            ):
                clean_content = clean_content + "\n"

            # Generate final summary when exiting thought block
            if (
                state.previous_block_type == "thought"
                and state.accumulated_thought_text
                and state.thought_chunk_count > 0
            ):
                # Only generate final summary if we haven't already generated one for this batch
                if state.thought_chunk_count % THOUGHT_CHUNKS_TRIGGER_K != 0 and on_summary_ready:
                    asyncio.create_task(
                        self._generate_thought_summary(
                            state.accumulated_thought_text,
                            state.current_block_index,
                            on_summary_ready,
                        )
                    )

            # Close previous block if one was started
            if state.content_block_started and state.previous_block_type is not None:
                block_stop = ContentBlockStop(index=state.current_block_index)
                yield block_stop
                self.conversation_manager.add_content_block_to_message(response_uuid, block_stop)
                logger.debug(
                    f"[_handle_block_transitions] Closing {state.previous_block_type} block at index={state.current_block_index}"
                )
                state.current_block_index += 1
                logger.debug(
                    f"[_handle_block_transitions] Incremented current_block_index to {state.current_block_index}"
                )
                state.content_block_started = False

                # Reset thought tracking after closing thought block
                if state.previous_block_type == "thought":
                    state.thought_chunk_count = 0
                    state.accumulated_thought_text = ""

                # Reset citation tracking when transitioning blocks
                state.yielded_citations.clear()
                state.full_text_buffer = ""

        # Start new block if needed (only if we detected a block type)
        if state.current_block_type is not None and not state.content_block_started:
            if state.current_block_type == "thought":
                block_start = ContentBlockStart(
                    index=state.current_block_index, content_block=ThoughtContent()
                )
            else:  # "text"
                block_start = ContentBlockStart(
                    index=state.current_block_index, content_block=TextContent()
                )

            logger.debug(
                f"[_handle_block_transitions] Starting {state.current_block_type} block at index={state.current_block_index}"
            )
            yield block_start
            self.conversation_manager.add_content_block_to_message(response_uuid, block_start)
            state.content_block_started = True

    async def _yield_content_delta(
        self,
        state: StreamState,
        clean_content: str,
        response_uuid: str,
        on_summary_ready: Optional[Callable[[int, str], Coroutine[Any, Any, None]]] = None,
    ) -> AsyncGenerator[ContentBlockDelta, None]:
        """Yield content delta based on block type"""
        if clean_content and state.current_block_type is not None:
            # Create appropriate delta based on block type
            if state.current_block_type == "thought":
                # Track thought chunks for summarization
                state.thought_chunk_count += 1
                state.accumulated_thought_text += clean_content + " "

                # Yield the thought delta
                content_block_delta = ContentBlockDelta(
                    index=state.current_block_index,
                    delta=ThoughtContentDelta(thinking=clean_content),
                )
                yield content_block_delta
                self.conversation_manager.add_content_block_to_message(
                    response_uuid, content_block_delta
                )

                # Check if we should generate a summary for this batch of chunks
                if state.thought_chunk_count % THOUGHT_CHUNKS_TRIGGER_K == 0 and on_summary_ready:
                    asyncio.create_task(
                        self._generate_thought_summary(
                            state.accumulated_thought_text,
                            state.current_block_index,
                            on_summary_ready,
                        )
                    )

            else:  # "text"
                content_block_delta = ContentBlockDelta(
                    index=state.current_block_index,
                    delta=TextContentDelta(text=clean_content),
                )
                yield content_block_delta
                self.conversation_manager.add_content_block_to_message(
                    response_uuid, content_block_delta
                )

    async def _handle_citations(
        self,
        state: StreamState,
        clean_content: str,
        response_uuid: str,
    ) -> AsyncGenerator[ContentBlockDelta, None]:
        """Parse and stream citations from accumulated text"""
        state.full_text_buffer += clean_content
        _, cited_node_ids, _ = await self._parse_citations_from_content(state.full_text_buffer)

        # Find new citations that haven't been yielded yet
        new_citations = [cit for cit in cited_node_ids if cit not in state.yielded_citations]

        # Stream new citations via CitationDelta
        if new_citations:
            citation_delta = ContentBlockDelta(
                index=state.current_block_index,
                delta=CitationDelta(citation=new_citations),
            )
            yield citation_delta
            self.conversation_manager.add_content_block_to_message(response_uuid, citation_delta)
            state.yielded_citations.update(new_citations)

    async def _process_summary_queue(
        self,
        state: StreamState,
        response_uuid: str,
    ) -> AsyncGenerator[ContentBlockDelta, None]:
        """Yield any pending summaries from queue"""
        while not state.summary_queue.empty():
            try:
                summary_result = state.summary_queue.get_nowait()
                summary_delta = ContentBlockDelta(
                    index=summary_result["index"],
                    delta=ThoughtSummaryDelta(
                        summary=ThoughtSummary(summary=summary_result["summary"])
                    ),
                )
                yield summary_delta
                self.conversation_manager.add_content_block_to_message(response_uuid, summary_delta)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.debug(f"Error yielding summary: {e}")

    async def _create_summary_callback(
        self, state: StreamState
    ) -> Callable[[int, str], Coroutine[Any, Any, None]]:
        """Create a callback function that wraps queue.put to match expected signature"""

        async def on_summary_ready(summary_index: int, summary_text: str) -> None:
            """Callback for background summary tasks - puts result in queue for main loop to yield"""
            await state.summary_queue.put({"index": summary_index, "summary": summary_text})

        return on_summary_ready

    # =========================================================================
    # TOOL EXECUTION & RESULTS METHODS
    # =========================================================================
    # Methods for executing tools, processing results, and managing tool calls

    def _extract_tool_call_description(self, func_call: Dict[str, Any]) -> Optional[str]:
        """Extract description field from tool call arguments"""
        if not func_call["description_yielded"]:
            desc_match = re.search(
                r'"description"\s*:\s*"((?:\\.|[^"\\])*)"',
                func_call["func_arguments"],
            )
            if desc_match:
                desc_value = desc_match.group(1)
                if desc_value:
                    logger.debug(f"✓ Description filled: {desc_value[:80]}")
                    return desc_value
                else:
                    logger.debug(f"✓ Description field present but empty")
            elif '"description"' in func_call["func_arguments"]:
                logger.debug(f"⏳ Description field is incomplete (no closing quote yet)")
        return None

    async def _yield_tool_descriptions(
        self,
        state: StreamState,
        response_uuid: str,
    ) -> AsyncGenerator[Union[ContentBlockDelta, ContentBlockStop], None]:
        """Parse tool arguments and yield descriptions and stops"""
        # Parse all function arguments first
        parsed_func_calls: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for func_call in state.func_calls:
            try:
                parsed_arguments = extract_json_from_llm_response(func_call["func_arguments"])
                parsed_func_calls.append((func_call, parsed_arguments))
            except Exception as e:
                logger.error(f"Failed to parse arguments for {func_call['func_name']}: {e}")

        # Store parsed calls in state for later execution
        state.parsed_func_calls = parsed_func_calls

        # Yield all function call descriptions and stops upfront
        for func_call, parsed_arguments in parsed_func_calls:
            try:
                content_delta = ContentBlockDelta(
                    index=func_call["index"],
                    delta=ToolUseBlockUpdateDelta(
                        message=parsed_arguments.get("description", func_call["func_name"]),
                        display_content=self.tool_manager.get_display_content(
                            func_call["func_name"], **parsed_arguments
                        ),
                    ),
                )
                yield content_delta
                self.conversation_manager.add_content_block_to_message(response_uuid, content_delta)

                content_stop = ContentBlockStop(index=func_call["index"])
                yield content_stop
                self.conversation_manager.add_content_block_to_message(response_uuid, content_stop)
            except Exception:
                pass

    async def _execute_tools_in_parallel(
        self,
        state: StreamState,
    ) -> List[Tuple[Dict[str, Any], ToolResultContent, Optional[Exception]]]:
        """Execute all parsed tool calls in parallel"""
        if not hasattr(state, "parsed_func_calls"):
            return []

        # Execute all tools in parallel
        async def execute_tool_async(
            func_call: Dict[str, Any], parsed_arguments: Dict[str, Any]
        ) -> Tuple[Dict[str, Any], ToolResultContent, Optional[Exception]]:
            """Execute a single tool in a thread pool to avoid blocking"""
            try:
                result = await asyncio.to_thread(
                    self.tool_manager.execute_function,
                    func_call["id"],
                    func_call["func_name"],
                    **parsed_arguments,
                )
                return func_call, result, None
            except Exception as e:
                logger.error(f"Error executing {func_call['func_name']}: {e}")
                return (
                    func_call,
                    ToolResultContent(
                        tool_call_id=func_call["id"],
                        name=func_call["func_name"],
                        message="Failed !",
                        content=[
                            {
                                "type": "text",
                                "text": f"Failed !: {e}",
                                "uuid": func_call["id"],
                            }
                        ],
                        is_error=True,
                    ),
                    e,
                )

        # Run all tool executions in parallel
        execution_tasks = [
            execute_tool_async(func_call, parsed_arguments)
            for func_call, parsed_arguments in state.parsed_func_calls
        ]
        execution_results = await asyncio.gather(*execution_tasks, return_exceptions=False)

        return execution_results

    async def _yield_error_message_block(
        self,
        state: StreamState,
        response_uuid: str,
        error_message: str,
        should_return: bool = True,
    ) -> AsyncGenerator[
        Union[ContentBlockStart, ContentBlockDelta, ContentBlockStop, MessageDelta], None
    ]:
        """Yield an error message as a content block with stream stop indicator.

        Handles the common pattern of stopping current block, creating error text block,
        stopping it, and sending a message delta with error stop reason.

        Args:
            state: Streaming state tracking content block status and current_block_index
            response_uuid: UUID of the response message
            error_message: The error message text to display
            should_return: If True, will signal caller to return after yielding

        Yields:
            Error content blocks and stop signal with error stop reason
        """
        # Stop current content block if one is active
        if state.content_block_started:
            block_stop = ContentBlockStop(index=state.current_block_index)
            yield block_stop
            self.conversation_manager.add_content_block_to_message(response_uuid, block_stop)
            logger.debug(
                f"[_yield_error_message_block] Closed previous block at index={state.current_block_index}"
            )
            state.current_block_index += 1
            logger.debug(
                f"[_yield_error_message_block] Incremented current_block_index to {state.current_block_index}"
            )
            state.content_block_started = False

        # Create error message block
        block_start = ContentBlockStart(
            index=state.current_block_index, content_block=TextContent()
        )
        logger.debug(
            f"[_yield_error_message_block] Starting error text block at index={state.current_block_index}"
        )
        yield block_start
        self.conversation_manager.add_content_block_to_message(response_uuid, block_start)
        state.content_block_started = True

        # Yield error message delta
        block_delta = ContentBlockDelta(
            index=state.current_block_index, delta=TextContentDelta(text=error_message)
        )
        yield block_delta
        self.conversation_manager.add_content_block_to_message(response_uuid, block_delta)

        # Stop error message block
        block_stop_final = ContentBlockStop(index=state.current_block_index)
        yield block_stop_final
        self.conversation_manager.add_content_block_to_message(response_uuid, block_stop_final)

        # Yield message delta with error stop reason to signal stream completion
        message_delta = MessageDelta(delta=MessageDeltaDelta(stop_reason="error"))
        yield message_delta
        self.conversation_manager.update_message(response_uuid, message_delta)

    async def _yield_tool_use_block(
        self,
        state: StreamState,
        tool_call: Any,
        response_uuid: str,
    ) -> AsyncGenerator[ContentBlockStart, None]:
        """Initialize a tool use block and yield its start events.

        Args:
            state: Streaming state with current_block_index tracking
            tool_call: The tool call delta
            response_uuid: UUID of the response message

        Yields:
            ContentBlockStart events for tool use and tool result blocks
        """
        # Manage content block transitions
        if state.content_block_started:
            logger.debug(
                f"[_yield_tool_use_block] Incrementing from previous block, current_block_index={state.current_block_index}"
            )
            state.current_block_index += 1
            logger.debug(
                f"[_yield_tool_use_block] Incremented current_block_index to {state.current_block_index}"
            )
        else:
            state.content_block_started = True

        # Yield tool use block start
        content_block = ContentBlockStart(
            index=state.current_block_index,
            content_block=ToolUseContent(
                id=tool_call.id,
                name=tool_call.function.name,
                message=tool_call.function.name,
            ),
        )
        logger.debug(
            f"[_yield_tool_use_block] Starting tool use block for {tool_call.function.name} at index={state.current_block_index}"
        )
        state.content_block_started = True
        yield content_block
        self.conversation_manager.add_content_block_to_message(response_uuid, content_block)

        # Track function call in state
        state.func_calls.append(
            {
                "id": tool_call.id,
                "func_name": tool_call.function.name,
                "func_arguments": "",
                "description_yielded": False,
                "index": state.current_block_index,
            }
        )
        logger.info(f"Tool call initiated: {tool_call.function.name}")

        # Yield tool result block start
        logger.debug(
            f"[_yield_tool_use_block] Incrementing for tool result block, current_block_index={state.current_block_index}"
        )
        state.current_block_index = state.current_block_index + 1
        logger.debug(
            f"[_yield_tool_use_block] Starting tool result block at index={state.current_block_index}"
        )
        content_block = ContentBlockStart(
            index=state.current_block_index,
            content_block=ToolResultContent(
                tool_call_id=tool_call.id,
                name=tool_call.function.name,
                content=[
                    {
                        "type": "text",
                        "text": "in processing...",
                        "uuid": tool_call.id,
                    }
                ],
            ),
        )
        self.conversation_manager.add_content_block_to_message(response_uuid, content_block)

    async def _yield_tool_arguments_and_description(
        self,
        state: StreamState,
        tool_call: Any,
        response_uuid: str,
    ) -> AsyncGenerator[ContentBlockDelta, None]:
        """Process tool arguments and yield description updates.

        Args:
            state: Streaming state with accumulated func_calls
            tool_call: The tool call delta with arguments
            response_uuid: UUID of the response message

        Yields:
            ContentBlockDelta for arguments and description
        """
        if not state.func_calls:
            return

        # Yield argument delta
        content_block = ContentBlockDelta(
            index=state.func_calls[-1]["index"],
            delta=InputJSONDelta(partial_json=tool_call.function.arguments),
        )
        state.content_block_started = True
        yield content_block
        self.conversation_manager.add_content_block_to_message(response_uuid, content_block)

        # Accumulate arguments
        state.func_calls[-1]["func_arguments"] += tool_call.function.arguments

        # Extract and yield tool description if available
        desc_value = self._extract_tool_call_description(state.func_calls[-1])
        if desc_value:
            content_block = ContentBlockDelta(
                index=state.func_calls[-1]["index"],
                delta=ToolUseBlockUpdateDelta(message=desc_value),
            )
            yield content_block
            self.conversation_manager.add_content_block_to_message(response_uuid, content_block)
            state.func_calls[-1]["description_yielded"] = True

    async def _yield_tool_result_blocks(
        self,
        func_call: Dict[str, Any],
        tool_result: ToolResultContent,
        response_uuid: str,
    ) -> AsyncGenerator[Union[ContentBlockStart, ContentBlockDelta, ContentBlockStop], None]:
        """Yield all content blocks for a tool result.

        Args:
            func_call: The tracked function call information
            tool_result: The result from tool execution
            response_uuid: UUID of the response message

        Yields:
            ContentBlockStart, ContentBlockDelta (for display content), and ContentBlockStop
        """
        result_index = func_call["index"] + 1

        # Yield tool result block start
        block_start = ContentBlockStart(
            index=result_index,
            content_block=tool_result,
        )
        yield block_start
        self.conversation_manager.add_content_block_to_message(response_uuid, block_start)

        # Yield display content if present
        if tool_result.display_content:
            block_delta = ContentBlockDelta(
                index=result_index,
                delta=InputJSONDelta(
                    partial_json=json.dumps(
                        [tool_result.display_content.model_dump(mode="json", exclude_none=True)]
                    )
                ),
            )
            yield block_delta

        # Yield tool result block stop
        block_stop = ContentBlockStop(index=result_index)
        yield block_stop
        self.conversation_manager.add_content_block_to_message(response_uuid, block_stop)

    async def _yield_tool_files_message(
        self,
        tool_result: ToolResultContent,
        response_uuid: str,
    ) -> Optional[MessageDelta]:
        """Yield a message delta with files if tool result contains files.

        Args:
            tool_result: The result from tool execution
            response_uuid: UUID of the response message

        Returns:
            MessageDelta if files are present, None otherwise
        """
        if not tool_result.files:
            return None

        message_delta = MessageDelta(
            delta=MessageDeltaDelta(stop_reason=None, files=tool_result.files)
        )
        self.conversation_manager.update_message(response_uuid, message_delta)
        return message_delta

    async def _handle_tool_execution_and_results(
        self,
        state: StreamState,
        response_uuid: str,
    ) -> AsyncGenerator[
        Union[ContentBlockStart, ContentBlockDelta, ContentBlockStop, MessageDelta], None
    ]:
        """Execute tools in parallel and yield results with error handling.

        Args:
            state: Streaming state with tracked tool calls
            response_uuid: UUID of the response message

        Yields:
            Tool result blocks and message deltas
        """
        # Execute tools in parallel
        execution_results = await self._execute_tools_in_parallel(state)

        # Process and yield results
        for func_call, tool_result, error in execution_results:
            try:
                # Skip on error
                if error:
                    logger.error(f"Tool execution failed for {func_call['func_name']}: {error}")
                    continue

                # Skip if no result
                if not tool_result:
                    continue

                # Yield all tool result blocks
                async for result_block in self._yield_tool_result_blocks(
                    func_call, tool_result, response_uuid
                ):
                    yield result_block

                # Yield file message delta if present
                files_message = await self._yield_tool_files_message(tool_result, response_uuid)
                if files_message:
                    yield files_message

            except Exception as e:
                logger.error(f"Error yielding results for {func_call['func_name']}: {e}")

    async def _process_tool_calls(
        self,
        state: StreamState,
        tool_calls: List[Any],
        response_uuid: str,
    ) -> AsyncGenerator[Union[ContentBlockStart, ContentBlockDelta], None]:
        """Process tool calls and yield tool-related content blocks.

        Args:
            state: Streaming state tracking tool calls and content blocks
            tool_calls: List of tool calls from the model delta
            response_uuid: UUID of the response message

        Yields:
            Content blocks for tool calls
        """

        for tool_call in list(tool_calls):
            # Process tool call initialization
            if tool_call.id and tool_call.function and tool_call.function.name:
                async for tool_block in self._yield_tool_use_block(state, tool_call, response_uuid):
                    yield tool_block

            # Process tool call arguments
            if tool_call.function and tool_call.function.arguments:
                async for arg_delta in self._yield_tool_arguments_and_description(
                    state, tool_call, response_uuid
                ):
                    yield arg_delta

    async def _process_content_delta(
        self,
        state: StreamState,
        delta_content: str,
        response_uuid: str,
        on_summary_ready: Optional[Callable[[int, str], Coroutine[Any, Any, None]]] = None,
    ) -> AsyncGenerator[
        Union[ContentBlockStart, ContentBlockDelta, ContentBlockStop, Literal["continue"]], None
    ]:
        """Process a content delta chunk and yield all associated deltas.

        Handles block transitions, content streaming, citation parsing, and summary queue processing.

        Args:
            state: Streaming state to track content block types and buffering
            delta_content: The content delta string from the model
            response_uuid: UUID of the response message
            index: Current content block index
            on_summary_ready: Optional callback for when thought summary is ready

        Yields:
            ContentBlockStart, ContentBlockDelta, and ContentBlockStop events
        """
        # logger.debug(f"Before State: {state}")
        # Process the content chunk (handles buffering and tag detection)
        clean_content, loop_continue = await self._process_content_chunk(
            state,
            delta_content,
        )
        if loop_continue:
            # logger.debug(f"loop_continue")
            yield "continue"
            return

        # Handle block transitions - yield immediately
        async for block_transition in self._handle_block_transitions(
            state,
            clean_content,
            response_uuid,
            on_summary_ready,
        ):
            # logger.debug(f"block_transistion: {block_transition}")
            yield block_transition

        # Yield content delta - yield immediately
        async for content_delta in self._yield_content_delta(
            state,
            clean_content,
            response_uuid,
            on_summary_ready,
        ):
            # logger.debug(f"content_delta: {content_delta}")
            yield content_delta

        # Handle citations - yield immediately
        async for citation_delta in self._handle_citations(
            state,
            clean_content,
            response_uuid,
        ):
            # logger.debug(f"citation_delta: {citation_delta}")
            yield citation_delta

        # Process summary queue - yield immediately
        async for summary_delta in self._process_summary_queue(state, response_uuid):
            # logger.debug(f"summary_delta: {summary_delta}")
            yield summary_delta

        # Clear pending content for next iteration
        state.pending_content = ""
        state.previous_block_type = state.current_block_type
        # logger.debug(f"After State: {state}")

    async def _handle_cost_calculation_and_logging(
        self,
        usage: Any,
        response_uuid: str,
        index: int,
    ) -> None:
        """Calculate OpenAI costs and log to service and database.

        Args:
            usage: CompletionUsage object from OpenAI response
            response_uuid: UUID of the response message
            index: Current chunk index
        """
        # Extract cached tokens if available
        cached_tokens = (
            usage.prompt_tokens_details.cached_tokens
            if usage.prompt_tokens_details and usage.prompt_tokens_details.cached_tokens
            else 0
        )

        # Calculate cost with prompt tokens excluding cached ones
        cost_usd = await self._calculate_openai_cost(
            usage.prompt_tokens - cached_tokens,
            usage.completion_tokens,
            cached_tokens,
        )

        if cost_usd:
            # Log service usage
            log_service(
                service_type=ServiceType.OPENAI,
                estimated_cost_usd=cost_usd,
                breakdown=usage.model_dump(exclude_none=True),  # type: ignore
                metadata={
                    "message_uuid": response_uuid,
                    "approx_content_index": index,
                },
            )

            # Update conversation model cost (thread-safe)
            with self._cost_lock:
                self.conversation_model.estimated_cost_usd += cost_usd

    async def _generate_response(
        self,
        response_uuid: str,
        personalized_styles: List[Style] = [],
        index: int = 0,
    ) -> tuple[
        AsyncGenerator[
            ContentBlockStart | ContentBlockDelta | MessageDelta | ContentBlockStop, None
        ],
        StreamState,
    ]:
        """
        Refactored response generation using OpenAI with tool orchestration.

        Returns a tuple of (response_generator, final_state) where:
        - response_generator: Yields all responses
        - final_state: Contains updated current_block_index from this and recursive calls

        Improves on the original by using centralized state management (StreamState)
        and extracting concerns into helper methods for better maintainability.

        Citations are parsed from the final agent response content.
        Files can be uploaded by user or created by assistant during conversation.
        """
        # Initialize streaming state - centralized management of all variables
        state = StreamState(current_block_index=index)
        on_summary_ready: Optional[Callable[[int, str], Coroutine[Any, Any, None]]] = None
        logger.debug(
            f"[_generate_response] Initialized state with current_block_index={state.current_block_index}, input index={index}"
        )

        async def response_generator():
            """Inner generator that yields responses and updates state."""
            nonlocal state, on_summary_ready

            try:
                # Get current message history with settings and styles for system prompt
                messages = self.conversation_manager.get_message_history(
                    response_uuid,
                    settings=self.conversation_model.settings,
                    personalized_styles=personalized_styles,
                )

                # Create OpenAI streaming request
                stream: AsyncStream[ChatCompletionChunk] = await self.client.chat.completions.create(  # type: ignore
                    model=self.model_name,
                    messages=messages,  # type: ignore
                    max_completion_tokens=8192,
                    temperature=0.9,
                    tools=self.tool_manager.get_tools(settings=self.conversation_model.settings, personalized_styles=personalized_styles),  # type: ignore
                    tool_choice="auto",
                    stream=True,
                    parallel_tool_calls=True,
                    stream_options={"include_usage": True},
                )

                # Create summary callback wrapper
                on_summary_ready = await self._create_summary_callback(state)

                if not isinstance(stream, AsyncStream):
                    raise Exception("Type Error with model output response")

                # Main streaming loop
                async for chunk in stream:  # type: ignore
                    if not isinstance(chunk, ChatCompletionChunk):
                        raise Exception("Expected ChatCompletionChunk in stream")
                    # logger.debug(f"{chunk}")

                    # Handle usage/cost tracking
                    if chunk.usage:
                        await self._handle_cost_calculation_and_logging(
                            chunk.usage, response_uuid, state.current_block_index
                        )
                        return

                    if chunk.choices and len(chunk.choices) > 0:  # type: ignore
                        choice = chunk.choices[0]
                        delta = choice.delta

                        # Process content delta
                        if delta.content:
                            async for delta_event in self._process_content_delta(
                                state,
                                delta.content,
                                response_uuid,
                                on_summary_ready,
                            ):
                                if isinstance(delta_event, str) and delta_event == "continue":
                                    continue
                                yield delta_event

                        # Process tool calls
                        if delta.tool_calls:
                            async for tool_event in self._process_tool_calls(
                                state, delta.tool_calls, response_uuid
                            ):
                                yield tool_event

                        if (
                            choice.finish_reason == "tool_calls"
                            and state.func_calls
                            and not state.tool_call_processed
                        ):
                            state.tool_call_processed = True

                            # Yield tool descriptions and stops
                            async for tool_block in self._yield_tool_descriptions(
                                state, response_uuid
                            ):
                                yield tool_block

                            # Execute tools, yield results, and handle files
                            async for result_block in self._handle_tool_execution_and_results(
                                state, response_uuid
                            ):
                                yield result_block

                            # Check for new messages or stop request before recursion
                            if not self._message_queue.empty():
                                # New messages in queue - end turn
                                message_delta = MessageDelta(
                                    delta=MessageDeltaDelta(stop_reason="end_turn")
                                )
                                yield message_delta
                                self.conversation_manager.update_message(
                                    response_uuid, message_delta
                                )
                            elif self._stop_requested:
                                # Stop request active - user interruption
                                message_delta = MessageDelta(
                                    delta=MessageDeltaDelta(stop_reason="user_interruption")
                                )
                                yield message_delta
                                self.conversation_manager.update_message(
                                    response_uuid, message_delta
                                )
                            # elif self._check_context_approaching_limit():
                            #     message_delta = MessageDelta(delta=MessageDeltaDelta(stop_reason="context_limit"))
                            #     yield message_delta
                            #     self.conversation_manager.update_message(response_uuid, message_delta)
                            else:
                                # Recursive call for follow-up - capture returned state with final index
                                try:
                                    logger.debug(
                                        f"[_generate_response] Before recursive call: current_block_index={state.current_block_index}"
                                    )
                                    child_responses, child_final_state = (
                                        await self._generate_response(
                                            response_uuid,
                                            personalized_styles,
                                            state.current_block_index + 1,
                                        )
                                    )

                                    async for response in child_responses:
                                        yield response

                                    logger.debug(
                                        f"[_generate_response] After recursive call: child_final_state.current_block_index={child_final_state.current_block_index}"
                                    )
                                    # Update parent state with child's final block index
                                    state.current_block_index = (
                                        child_final_state.current_block_index
                                    )
                                    logger.debug(
                                        f"[_generate_response] Updated parent state: current_block_index={state.current_block_index}"
                                    )

                                except json.JSONDecodeError as e:
                                    logger.error(f"Error parsing function arguments - {e}")
                                    error_message = "I had trouble processing that request. How else can I help you?"
                                    async for error_block in self._yield_error_message_block(
                                        state, response_uuid, error_message, should_return=True
                                    ):
                                        yield error_block
                                    return

                                except Exception as e:
                                    logger.error(
                                        f"Error executing function {state.func_calls}: {e}",
                                        exc_info=True,
                                    )
                                    error_message = "I apologize, but I encountered an issue. How else can I help you?"
                                    async for error_block in self._yield_error_message_block(
                                        state, response_uuid, error_message, should_return=True
                                    ):
                                        yield error_block
                                    return

                        elif choice.finish_reason == "stop":
                            # Yield any final summaries from queue
                            async for summary_delta in self._process_summary_queue(
                                state, response_uuid
                            ):
                                yield summary_delta

                            if state.content_block_started:
                                content_block = ContentBlockStop(index=state.current_block_index)
                                yield content_block
                                self.conversation_manager.add_content_block_to_message(
                                    response_uuid, content_block
                                )
            except Exception as e:
                logger.error(f"Error in _generate_response: {e}", exc_info=True)
                error_message = "I apologize, but I encountered an issue. How else can I help you?"
                async for error_block in self._yield_error_message_block(
                    state, response_uuid, error_message, should_return=False
                ):
                    yield error_block
            finally:
                logger.debug(
                    f"[_generate_response] Final state before return: current_block_index={state.current_block_index}"
                )

        return response_generator(), state

    async def _generate_related_questions(
        self, num_turns: int = 5, num_questions: int = 3
    ) -> List[str]:
        """
        Generate related follow-up questions based on the last N turns in chat history.

        Args:
            num_turns: Number of recent message turns to consider (default: 5)
            num_questions: Number of related questions to generate (default: 3)

        Returns:
            List of related follow-up questions
        """
        try:
            # Get the last num_turns messages from conversation history
            all_messages = self.conversation_manager.get_user_history(limit=num_turns * 2)

            if not all_messages:
                logger.warning("No messages found to generate related questions")
                return []

            # Build conversation context from recent messages
            conversation_parts: List[str] = []
            for message in list(all_messages.values())[-num_turns:]:  # Get last num_turns
                message_dict = message.model_dump(for_model=True, mode="json", exclude_none=True)
                for msg in message_dict.get("content", []):
                    role = msg.get("role", "user").upper()
                    content = msg.get("content", "")
                    if content:
                        conversation_parts.append(f"{role}: {content}")  # Limit content length

            conversation_text = "\n\n".join(conversation_parts)

            if not conversation_text.strip():
                logger.warning("No text content found in recent messages to generate questions")
                return []

            # Prepare question generation prompt
            system_prompt = f"""You are a helpful assistant that generates insightful follow-up questions.

Generate exactly {num_questions} thought-provoking follow-up questions based on the conversation provided.
These questions should:
1. Build upon the current discussion
2. Explore related topics or deeper insights
3. Be concise and clear (max 15 words each)
4. Encourage further exploration

Format your response as a JSON array of strings: ["question1", "question2", "question3"]"""

            user_prompt = f"""Based on the following recent conversation, generate {num_questions} related follow-up questions.

Conversation (last {num_turns} turns):
{conversation_text}

Generate related questions:"""

            # Call OpenAI API for question generation
            if self.client:
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=300,
                )
            else:
                logger.warning("Client not initialized for question generation")
                return []

            # Parse response
            response_text = response.choices[0].message.content
            if response.usage:
                usage_dict = response.usage.model_dump(exclude_none=True)
                if usage_dict:
                    cost_usd = await self._calculate_openai_cost(
                        usage_dict.get("prompt_tokens", 0)
                        - usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                        usage_dict.get("completion_tokens", 0),
                        usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                    )

                    if cost_usd:
                        log_service(
                            service_type=ServiceType.OPENAI,
                            estimated_cost_usd=cost_usd,
                            breakdown=usage_dict,
                            description="Related Questions Generation",
                        )
                        with self._cost_lock:
                            self.conversation_model.estimated_cost_usd += cost_usd

            if not response_text:
                return []

            try:
                # Extract JSON array from response
                json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
                if json_match:
                    questions: List[str] = json.loads(json_match.group())
                    # Ensure we have a list of strings
                    if isinstance(questions, list):  # type: ignore
                        questions = [str(q).strip() for q in questions if q]
                        logger.info(f"Generated {len(questions)} related questions")
                        return questions[:num_questions]  # Ensure we don't exceed requested number
                    else:
                        return []
                else:
                    logger.warning("Could not extract JSON array from question generation response")
                    return []
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON from question generation response: {e}")
                logger.debug(f"Response text: {response_text}")
                return []

        except Exception as e:
            logger.error(f"Error in generate_related_questions(): {e}", exc_info=True)
            return []

    async def _summarize(self) -> None:
        """
        Incrementally update the conversation summary using existing summary and last message turn.
        """
        try:
            logger.debug("Starting conversation summary update")

            if not self.client:
                logger.debug("Client not initialized, skipping summary update")
                return

            # Get last message turn
            all_messages = self.conversation_manager.get_user_history(limit=2)
            if not all_messages:
                logger.debug("No messages found to summarize")
                return

            # Extract last message turn
            message_list = list(all_messages.values())
            if len(message_list) < 1:
                logger.debug("Empty message list, skipping summary")
                return

            last_message = message_list[-1]
            message_dict = last_message.model_dump(for_model=True, mode="json", exclude_none=True)
            last_message_text = ""
            for msg in message_dict.get("content", []):
                content = msg.get("content", "")
                if content:
                    last_message_text += content + "\n"

            if not last_message_text.strip():
                logger.debug("No text content in last message, skipping summary")
                return

            # Build context with existing summary
            existing_summary = self.conversation_model.summary or "No previous summary"
            logger.debug(f"Existing summary length: {len(existing_summary)} chars")

            system_prompt = """You are a helpful assistant that generates concise conversation summaries.
Update the existing summary with new information from the latest message turn.
Keep the summary to 10-15 sentences maximum.
Focus on the core topics, goals, and outcomes."""

            user_prompt = f"""Update this conversation summary with the latest message turn:

Existing Summary:
{existing_summary}

Latest Message:
{last_message_text}

Provide only the updated summary as plain text (no JSON)."""

            logger.info(f"Calling OpenAI API for summary update (model: {self.model_name})")
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=200,
            )

            response_text = response.choices[0].message.content
            if not response_text:
                logger.warning("Empty response from OpenAI API for summary update")
                return

            logger.debug(f"Received summary response: {len(response_text)} characters")

            if response.usage:
                usage_dict = response.usage.model_dump(exclude_none=True)
                if usage_dict:
                    cost_usd = await self._calculate_openai_cost(
                        usage_dict.get("prompt_tokens", 0)
                        - usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                        usage_dict.get("completion_tokens", 0),
                        usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                    )

                    if cost_usd:
                        logger.debug(f"Summary update cost: ${cost_usd:.6f}")
                        log_service(
                            service_type=ServiceType.OPENAI,
                            estimated_cost_usd=cost_usd,
                            breakdown=usage_dict,
                            description="Conversation Summary Update",
                        )
                        with self._cost_lock:
                            self.conversation_model.estimated_cost_usd += cost_usd

            self.conversation_model.summary = response_text.strip()
            logger.info(
                f"Successfully updated summary: {str(self.conversation_model.summary)[:100]}..."
            )
            logger.debug("Conversation model marked for DB sync (summary updated)")

        except Exception as e:
            logger.error(f"Error in summarize(): {e}", exc_info=True)

    async def _generate_conversation_name(self) -> None:
        """
        Generate a conversation name if one doesn't exist.
        Uses the last message turn to infer an appropriate name.
        """
        logger.debug("Starting conversation name generation")

        if self.conversation_model.name is not None:
            logger.debug(f"Conversation already has name: {self.conversation_model.name}")
            return

        try:
            # Get last message turn
            all_messages = self.conversation_manager.get_user_history(limit=2)
            if not all_messages:
                logger.debug("No messages found for name generation")
                return

            last_message = list(all_messages.values())[-1]
            message_dict = last_message.model_dump(for_model=True, mode="json", exclude_none=True)
            last_message_text = ""
            for msg in message_dict.get("content", []):
                content = msg.get("content", "")
                if content:
                    last_message_text += content + "\n"

            if not last_message_text.strip():
                logger.debug("No text content in last message for name generation")
                return

            if not self.client:
                logger.warning("Client not initialized, skipping name generation")
                return

            system_prompt = """You are a helpful assistant that generates concise conversation names.
Create a short, descriptive name (3-5 words max) that captures the primary topic or goal.
Use title case formatting.
Return only the name, nothing else."""

            user_prompt = f"""Generate a short conversation name based on this message:

{last_message_text}"""

            logger.info(f"Calling OpenAI API for name generation (model: {self.model_name})")
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=20,
            )

            response_text = response.choices[0].message.content
            logger.debug(f"Received name generation response: '{response_text}'")

            if response.usage:
                usage_dict = response.usage.model_dump(exclude_none=True)
                if usage_dict:
                    cost_usd = await self._calculate_openai_cost(
                        usage_dict.get("prompt_tokens", 0)
                        - usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                        usage_dict.get("completion_tokens", 0),
                        usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                    )

                    if cost_usd:
                        logger.debug(f"Name generation cost: ${cost_usd:.6f}")
                        log_service(
                            service_type=ServiceType.OPENAI,
                            estimated_cost_usd=cost_usd,
                            breakdown=usage_dict,
                            description="Conversation Name Generation",
                        )
                        with self._cost_lock:
                            self.conversation_model.estimated_cost_usd += cost_usd

            if response_text:
                self.conversation_model.name = response_text.strip()
                logger.info(
                    f"Successfully generated conversation name: {self.conversation_model.name}"
                )
                logger.debug("Conversation model marked for DB sync (name updated)")

        except Exception as e:
            logger.error(f"Error in _generate_conversation_name(): {e}", exc_info=True)

    async def delete_messages(self):
        """Asynchronously delete all messages in the conversation."""
        self.conversation_manager.delete_messages()
        await self.push_to_db()
