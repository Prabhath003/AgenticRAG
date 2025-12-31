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
from typing import List, Dict, Any, Optional, Callable, AsyncGenerator, Tuple
import json
import tiktoken
from openai import AsyncOpenAI, AsyncAzureOpenAI

from .custom_types import ResponseResponse, Utterance, ResponseRequiredRequest
from ...config import Config
from ...log_creator import get_file_logger
from ..rag_system import get_rag_system
from ..entity_scoped_rag import get_entity_rag_manager
from ...infrastructure.metrics import Service, ServiceType

logger = get_file_logger()

# Initialize tiktoken encoding for token estimation
try:
    tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
except Exception:
    tiktoken_encoding = None

class ResearchAgent:
    """Single inference research agent using RAG navigation tools"""

    def __init__(self, id: str, entity_name: str, use_entity_scoped: bool = True, on_stage_change_callback: Optional[Callable[..., Any]]=None, entity_dir: Optional[str] = None):
        # Initialize OpenAI or Azure OpenAI client based on availability
        self.client = None
        self.use_azure = False

        # Try Azure OpenAI first if all required credentials are available
        if (Config.AZURE_OPENAI_ENDPOINT and
            Config.AZURE_OPENAI_KEY and
            Config.AZURE_OPENAI_DEPLOYMENT):
            try:
                self.client = AsyncAzureOpenAI(
                    azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
                    api_key=Config.AZURE_OPENAI_KEY,
                    api_version=Config.AZURE_OPENAI_VERSION
                )
                self.use_azure = True
                self.model_name = Config.AZURE_OPENAI_DEPLOYMENT
                logger.info(f"Initialized ResearchAgent with Azure OpenAI (deployment: {self.model_name})")
            except Exception as e:
                logger.warning(f"Failed to initialize Azure OpenAI client: {e}. Falling back to OpenAI...")
                self.client = None

        # Fall back to OpenAI if Azure is not available or failed
        if self.client is None and Config.OPENAI_API_KEY:
            try:
                self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
                self.use_azure = False
                self.model_name = Config.GPT_MODEL
                logger.info(f"Initialized ResearchAgent with OpenAI (model: {self.model_name})")
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

        self.id = id
        self.conversation_history: List[Dict[str, Any]] = []

        self.entity_name = entity_name
        self.use_entity_scoped = use_entity_scoped
        self.on_stage_change_callback = on_stage_change_callback
        self.entity_dir = entity_dir

        # Use entity-scoped RAG for better performance if enabled
        if use_entity_scoped:
            self.entity_rag_manager = get_entity_rag_manager()
            self.entity_store = self.entity_rag_manager.get_entity_store(id, entity_dir)
            self.rag_system = None  # Not using global RAG
            logger.info(f"Initialized ResearchAgent with entity-scoped RAG for {entity_name} (ID: {id})")
        else:
            self.rag_system = get_rag_system()
            self.entity_rag_manager = None
            self.entity_store = None
            logger.info(f"Initialized ResearchAgent with global RAG for {entity_name} (ID: {id})")

        # Generate system prompt
        self.system_prompt = self._generate_system_prompt()

    def parse_citations_from_content(self, content: str) -> tuple[str, List[str], List[Dict[str, Any]]]:
        """
        Parse inline citations from content and return cleaned content + citations

        Citation format: [[N](node_id)]
        Example: "Revenue grew 25% [[1](company123_doc_45678_3)]"

        Returns:
            Tuple of (cleaned_content, cited_node_ids, citations_list)
        """
        import re

        # Pattern to match [[N](node_id)]
        citation_pattern = r'\[(\d+)\]\(([^)]+)\)'

        cited_node_ids: List[str] = []
        citations: List[Dict[str, Any]] = []
        citation_map: Dict[int, str] = {}

        # Find all citations
        matches = re.finditer(citation_pattern, content)
        for match in matches:
            citation_num = int(match.group(1))
            node_id = match.group(2)

            if node_id not in cited_node_ids:
                cited_node_ids.append(node_id)

            if citation_num not in citation_map:
                citation_map[citation_num] = node_id

                # Parse node_id to get components
                parts = node_id.split('_')
                doc_id = parts[1] if len(parts) > 1 else 'unknown'
                chunk_idx = parts[-1] if len(parts) > 2 else 'unknown'

                citations.append({
                    'citation_number': citation_num,
                    'node_id': node_id,
                    'doc_id': doc_id,
                    'chunk_index': chunk_idx
                })

        # Don't remove citations from content - keep them visible
        return content, cited_node_ids, citations

    def add_to_messages(self, messages: List[Dict[str, str]], utterance: Utterance):
        if utterance.role == "agent":
            if messages and messages[-1]["role"] == "assistant":
                messages[-1]["content"] = utterance.content
            else:
                messages.append({"role": "assistant", "content": utterance.content})
        else:
            if utterance.content.strip():
                if messages and messages[-1]["role"] == "user":
                    messages[-1]["content"] += " " + utterance.content
                else:
                    messages.append({"role": "user", "content": utterance.content})
            else:
                if messages and messages[-1]["role"] == "user":
                    messages[-1]["content"] += " ..."
                else:
                    messages.append({"role": "user", "content": "..."})
        return messages
    
    def convert_transcript_to_openai_messages(self, transcript: List[Utterance]):
        messages: List[Dict[str, Any]] = []
        for utterance in transcript:
            if utterance.role == "agent":
                messages.append({"role": "assistant", "content": utterance.content})
            else:
                messages.append({"role": "user", "content": utterance.content})
        return messages

    def _get_model_pricing(self, model_name: str) -> Tuple[float, float, float]:
        """
        Get input, output, and cache token pricing for different OpenAI models.

        Args:
            model_name: Name of the model (e.g., 'gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini')

        Returns:
            Tuple of (input_price_per_1M_tokens, output_price_per_1M_tokens, cache_read_price_per_1M_tokens)
            Cache read price is 90% discount on input price, but we return it explicitly for clarity.
        """
        # Normalize model name for comparison
        model_lower = model_name.lower()

        # Pricing dictionary for different model families
        # Format: 'model': (input_price, output_price, cache_read_price)
        # Cache read price is typically 10% of input price (90% discount)
        pricing_map = {
            # GPT-4o family
            'gpt-4o': (2.5, 10.0, 1.25),  # $5/1M input, $15/1M output, $0.50/1M cache read

            # GPT-4.1 family
            'gpt-4.1': (2.0, 8, 0.5),  # $6/1M input, $18/1M output, $0.60/1M cache read

            # GPT-4o-mini family (cheapest)
            'gpt-4o-mini': (0.15, 0.60, 0.075),  # $0.15/1M input, $0.60/1M output, $0.015/1M cache read
            'gpt-4.1-mini': (0.4, 1.6, 0.1),  # Same as 4o-mini

            # Speculative future models
            'gpt-5-mini': (0.25, 2, 0.025),  # Speculative pricing
        }

        # Check for exact match first
        if model_lower in pricing_map:
            return pricing_map[model_lower]

        # Check for partial matches (e.g., "gpt-4o" in "gpt-4o-mini-2024-07-18")
        for model_key, pricing in pricing_map.items():
            if model_key in model_lower:
                logger.debug(f"Using pricing for '{model_key}' based on model name '{model_name}'")
                return pricing

        # Default to gpt-4o pricing if model not recognized
        logger.warning(f"Model '{model_name}' not found in pricing map. Using gpt-4o pricing as fallback.")
        return (5.0, 15.0, 0.5)

    def _calculate_openai_cost(self, input_tokens: int, output_tokens: int, cache_tokens: int=0) -> float:
        """
        Calculate OpenAI API cost based on model and token usage, including cached tokens.
        Pricing varies by model (gpt-4o, gpt-4o-mini, gpt-4.1-mini, etc.)

        Cache tokens are charged at different rates from OpenAI:
        - Cache creation: same price as regular input tokens
        - Cache read: 90% discount (10% of regular input price)

        Args:
            input_tokens: Number of regular input tokens (not cached)
            output_tokens: Number of output tokens
            cache_creation_tokens: Tokens cached during this request (optional)
            cache_read_tokens: Tokens read from cache (optional)

        Returns:
            Total cost in USD, rounded to 6 decimal places
        """
        input_token_price, output_token_price, cache_token_price = self._get_model_pricing(self.model_name)

        # Regular input token cost
        input_cost = (input_tokens / 1_000_000) * input_token_price

        # Cache token cost
        cache_cost = (cache_tokens / 1_000_000) * cache_token_price

        # Output token cost
        output_cost = (output_tokens / 1_000_000) * output_token_price

        total_cost = input_cost + cache_cost + output_cost

        return round(total_cost, 6)

    def _estimate_tokens_from_content(self, content: str) -> int:
        """Estimate token count from text content using tiktoken.
        Falls back to character-based approximation if tiktoken is unavailable.
        Used when OpenAI API doesn't return usage information.
        """
        if not content:
            return 0

        if tiktoken_encoding:
            try:
                return len(tiktoken_encoding.encode(content))
            except Exception as e:
                logger.warning(f"Failed to estimate tokens with tiktoken: {e}")

        # Fallback: rough approximation - 1 token ≈ 4 characters
        return max(1, len(content) // 4)

    async def research_question(self, request: ResponseRequiredRequest, func_result: Optional[Dict[str, Any]]=None, node_ids: Optional[List[str]]=None, relationship_ids: Optional[List[Dict[str, str]]]=None) -> AsyncGenerator[ResponseResponse, None]:
        """Draft response for voice conversation using OpenAI with service tracking"""

        # Initialize tracking lists if not provided
        if node_ids is None:
            node_ids = []
        if relationship_ids is None:
            relationship_ids = []
        cumulative_services = []

        messages: List[Dict[str, Any]] = request.transcript

        # Track OpenAI usage for this call
        total_input_tokens = 0
        total_output_tokens = 0
        try:
            # Add system message
            if messages:
                if messages[0]['role'] != 'system':
                    system_message = {"role": "system", "content": self.system_prompt}
                    messages = [system_message] + messages
            
            # logger.debug(f"{json.dumps(messages, indent=2)}")
            
            if func_result:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": func_result["id"],
                        "type": "function",
                        "function": {
                            "name": func_result["func_name"],
                            "arguments": json.dumps(func_result["arguments"])
                        }
                    }]
                })
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": func_result["id"],
                    "content": str(func_result["result"]) if func_result["result"] else ''
                })

            stream = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=8192,
                temperature=0.9,
                tools=self.get_tools(),
                tool_choice="auto",
                stream=True,
                stream_options={"include_usage": True}
            )

            func_call = None
            func_arguments = ""
            content_buffer = ""
            tool_call_processed = False
            final_usage_received = False
            cache_creation_tokens = 0
            cache_read_tokens = 0

            async for chunk in stream:
                # Track token usage from stream (usage comes in final chunk)
                # logger.debug(f"{chunk}")
                if chunk.usage:
                    usage_dict:Dict[str, Any] = chunk.usage.model_dump() if chunk.usage else {}
                    # logger.debug(f'{usage_dict}')
                    if usage_dict:
                        cost_usd = self._calculate_openai_cost(
                            usage_dict.get("prompt_tokens", 0) - usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                            usage_dict.get("completion_tokens", 0),
                            usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                        )
                    service = Service(
                        service_type=ServiceType.OPENAI,
                        breakdown=usage_dict,
                        estimated_cost_usd=cost_usd
                    )
                    cumulative_services.append(service)
                    yield ResponseResponse(
                        response_type="usage",
                        response_id=request.response_id,
                        content="",
                        content_complete=True,
                        end_call=True,
                        node_ids=node_ids,
                        relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                        cited_node_ids=[],
                        citations=[],
                        services_used=[s.to_dict() for s in cumulative_services],
                        estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services)
                    )
                    return

                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta.content:
                        content_buffer += delta.content

                        # # Calculate cumulative cost for OpenAI (including cached tokens)
                        # openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens,
                        #                                           cache_creation_tokens, cache_read_tokens)

                        # # Always include OpenAI service in streaming responses
                        # openai_service = Service(ServiceType.OPENAI, {
                        #     "input_tokens": total_input_tokens,
                        #     "output_tokens": total_output_tokens,
                        #     "total_tokens": total_input_tokens + total_output_tokens,
                        #     "cache_creation_tokens": cache_creation_tokens,
                        #     "cache_read_tokens": cache_read_tokens,
                        #     "has_usage_info": final_usage_received
                        # }, openai_cost)

                        response = ResponseResponse(
                            response_id=request.response_id,
                            content=delta.content,
                            content_complete=False,
                            end_call=False,
                            node_ids=node_ids,
                            relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                            cited_node_ids=[],  # Will be populated at the end
                            citations=[],  # Will be populated at the end
                            services_used=[s.to_dict() for s in cumulative_services],
                            estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services)
                            # services_used=[s.to_dict() for s in cumulative_services] + [openai_service.to_dict()],
                            # estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
                        )
                        messages = self.add_to_messages(messages, Utterance(role="agent", content=content_buffer))
                        yield response
                    
                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            if tool_call.id:
                                func_call = {
                                    "id": tool_call.id,
                                    "func_name": tool_call.function.name,
                                    "arguments": ""
                                }
                                func_arguments = ""
                                logger.info(f"Tool call initiated: {tool_call.function.name}")
                            
                            if tool_call.function.arguments:
                                func_arguments += tool_call.function.arguments
                    
                    if choice.finish_reason == "tool_calls" and func_call and not tool_call_processed:
                        tool_call_processed = True
                        try:
                            parsed_arguments = json.loads(func_arguments)
                            func_call["arguments"] = parsed_arguments
                            
                            result, node_ids, relationship_ids, cumulative_services = await self.execute_function(
                                func_call["func_name"],
                                func_call["arguments"],
                                node_ids,
                                relationship_ids,
                                cumulative_services
                            )
                            
                            if isinstance(result, dict):
                                if result.get("action") == "end_call":
                                    logger.info(f"Tool-based end call with message: {result['message']}")
                                    # Calculate final cost including OpenAI
                                    # openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens,
                                    #                                           cache_creation_tokens, cache_read_tokens)
                                    response = ResponseResponse(
                                        response_type="update",
                                        response_id=request.response_id,
                                        content=result["message"].strip() + "\n",
                                        content_complete=True,
                                        end_call=True,
                                        node_ids=node_ids,
                                        relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                                        cited_node_ids=[],
                                        citations=[],
                                        services_used=[s.to_dict() for s in cumulative_services],
                                        estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services)
                                        # services_used=[s.to_dict() for s in cumulative_services] + [
                                        #     Service(ServiceType.OPENAI, {
                                        #         "input_tokens": total_input_tokens,
                                        #         "output_tokens": total_output_tokens,
                                        #         "total_tokens": total_input_tokens + total_output_tokens,
                                        #         "has_usage_info": final_usage_received
                                        #     }, openai_cost).to_dict()
                                        # ],
                                        # estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
                                    )
                                    # messages = self.add_to_messages(messages, Utterance(role="agent", content=result["message"]))
                                    yield response
                            
                            if parsed_arguments.get("message") and not isinstance(result, dict):
                                # openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens,
                                #                                           cache_creation_tokens, cache_read_tokens)
                                response = ResponseResponse(
                                    response_type="update",
                                    response_id=request.response_id,
                                    content=parsed_arguments["message"].strip() + "\n",
                                    content_complete=False,
                                    end_call=False,
                                    node_ids=node_ids,
                                    relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                                    cited_node_ids=[],
                                    citations=[],
                                    services_used=[s.to_dict() for s in cumulative_services],
                                    estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services)
                                    # services_used=[s.to_dict() for s in cumulative_services] + [
                                    #     Service(ServiceType.OPENAI, {
                                    #         "input_tokens": total_input_tokens,
                                    #         "output_tokens": total_output_tokens,
                                    #         "total_tokens": total_input_tokens + total_output_tokens,
                                    #         "has_usage_info": final_usage_received
                                    #     }, openai_cost).to_dict()
                                    # ],
                                    # estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
                                )
                                # messages = self.add_to_messages(messages, Utterance(role="agent", content=parsed_arguments["message"]))
                                yield response
                            
                            func_result_data: Dict[str, Any] = {
                                "id": func_call["id"],
                                "arguments": func_call["arguments"],
                                "func_name": func_call["func_name"],
                                "result": result,
                            }

                            recursive_cumulative_services = []
                            async for response in self.research_question(ResponseRequiredRequest(
                                    interaction_type="response_required",
                                    response_id=request.response_id,
                                    transcript=messages
                                ), func_result_data, node_ids, relationship_ids):
                                # cumulative_services.extend([Service.from_dict(service) for service in response.services_used])
                                # response.services_used = [s.to_dict() for s in cumulative_services]
                                # response.estimated_cost_usd = sum(s.estimated_cost_usd for s in cumulative_services)
                                recursive_cumulative_services = [Service.from_dict(service) for service in response.services_used]
                                yield response
                            cumulative_services.extend(recursive_cumulative_services)
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"Error parsing function arguments: {func_arguments} - {e}")
                            openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens)
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content="I had trouble processing that request. How else can I help you?",
                                content_complete=True,
                                end_call=False,
                                node_ids=node_ids,
                                relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                                cited_node_ids=[],
                                citations=[],
                                services_used=[s.to_dict() for s in cumulative_services] + [
                                        Service(ServiceType.OPENAI, {
                                            "input_tokens": total_input_tokens,
                                            "output_tokens": total_output_tokens,
                                            "total_tokens": total_input_tokens + total_output_tokens,
                                            "has_usage_info": final_usage_received
                                        }, openai_cost).to_dict()
                                    ],
                                estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
                            )
                            messages = self.add_to_messages(messages, Utterance(role="agent", content="I had trouble processing that request. How else can I help you?"))
                            yield response
                            return
                        except Exception as e:
                            logger.error(f"Error executing function {func_call['func_name']}: {e}", exc_info=True)
                            openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens)
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content="I encountered an issue while processing your request. How else can I help you?",
                                content_complete=True,
                                end_call=False,
                                node_ids=node_ids,
                                relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                                cited_node_ids=[],
                                citations=[],
                                services_used=[s.to_dict() for s in cumulative_services] + [
                                        Service(ServiceType.OPENAI, {
                                            "input_tokens": total_input_tokens,
                                            "output_tokens": total_output_tokens,
                                            "total_tokens": total_input_tokens + total_output_tokens,
                                            "has_usage_info": final_usage_received
                                        }, openai_cost).to_dict()
                                    ],
                                estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
                            )
                            messages = self.add_to_messages(messages, Utterance(role="agent", content="I encountered an issue while processing your request. How else can I help you?"))
                            yield response
                            return
                    
                    elif choice.finish_reason == "stop":
                        # Parse citations from the complete response
                        _, cited_node_ids, citations = self.parse_citations_from_content(content_buffer)

                        # # If usage info was not received, estimate from content
                        # if not final_usage_received:
                        #     logger.warning(f"No usage information received from OpenAI API for session {request.response_id}. Using token estimation.")
                        #     # Estimate output tokens from response content
                        #     total_output_tokens = self._estimate_tokens_from_content(content_buffer)
                        #     # Estimate input tokens from conversation history
                        #     for msg in messages:
                        #         if isinstance(msg, dict) and msg.get("content"):
                        #             total_input_tokens += self._estimate_tokens_from_content(str(msg.get("content", "")))

                        # Calculate final cost including OpenAI
                        # openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens,
                        #                                           cache_creation_tokens, cache_read_tokens)

                        # Always include OpenAI service (even if tokens are 0, still track the request)
                        # openai_service = Service(ServiceType.OPENAI, {
                        #     "input_tokens": total_input_tokens,
                        #     "output_tokens": total_output_tokens,
                        #     "total_tokens": total_input_tokens + total_output_tokens,
                        #     "cache_creation_tokens": cache_creation_tokens,
                        #     "cache_read_tokens": cache_read_tokens,
                        #     "has_usage_info": final_usage_received,
                        #     "tokens_estimated": not final_usage_received  # Flag to show tokens were estimated
                        # }, openai_cost)

                        response = ResponseResponse(
                            response_id=request.response_id,
                            content="",
                            content_complete=True,
                            end_call=False,
                            node_ids=node_ids,
                            relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                            cited_node_ids=cited_node_ids,
                            citations=citations,
                            services_used=[s.to_dict() for s in cumulative_services],
                            estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services)
                            # services_used=[s.to_dict() for s in cumulative_services] + [openai_service.to_dict()],
                            # estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
                        )
                        messages = self.add_to_messages(messages, Utterance(role="agent", content=content_buffer))
                        yield response

        except Exception as e:
            logger.error(f"Error in draft_response: {e}")

            # If usage info was not received, estimate from content
            if not final_usage_received and total_output_tokens == 0:
                logger.warning(f"No usage information received from OpenAI API. Using token estimation.")
                # For error responses, estimate conservatively
                total_output_tokens = self._estimate_tokens_from_content("I apologize, but I encountered an issue. How else can I help you with finding courses?")
                # Estimate input tokens from conversation history
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("content"):
                        total_input_tokens += self._estimate_tokens_from_content(str(msg.get("content", "")))

            openai_cost = self._calculate_openai_cost(total_input_tokens, total_output_tokens)
            response = ResponseResponse(
                response_id=request.response_id,
                content="I apologize, but I encountered an issue. How else can I help you with finding courses?",
                content_complete=True,
                end_call=False,
                node_ids=node_ids,
                relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                cited_node_ids=[],
                citations=[],
                services_used=[s.to_dict() for s in cumulative_services] + [
                    Service(ServiceType.OPENAI, {
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "total_tokens": total_input_tokens + total_output_tokens,
                        "cache_creation_tokens": cache_creation_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "has_usage_info": final_usage_received,
                        "tokens_estimated": not final_usage_received
                    }, openai_cost).to_dict()
                ],
                estimated_cost_usd=sum(s.estimated_cost_usd for s in cumulative_services) + openai_cost
            )
            messages = self.add_to_messages(messages, Utterance(role="agent", content="I apologize, but I encountered an issue. How else can I help you?"))
            yield response

    def _generate_system_prompt(self) -> str:
        """Generate system prompt for company intelligence"""
        performance_note = " (OPTIMIZED: Using entity-scoped RAG for 10-100x faster search!)" if self.use_entity_scoped else ""

        return f"""You are AI agent, an AI-powered business intelligence assistant specializing in comprehensive company research{performance_note}.

**Your Mission:** Research {self.entity_name} thoroughly by strategically using multiple RAG navigation tools to gather detailed, accurate information from company documents.

**Available RAG Navigation Tools:**
1. **semantic_search_within_entity** - Primary search tool to find relevant chunks
2. **get_previous_chunk** - Navigate to previous chunk in same document
3. **get_next_chunk** - Navigate to next chunk in same document
4. **get_chunk_context** - Get surrounding chunks (previous + current + next)
5. **get_entity_documents** - List all available documents for the company (returns doc_id and doc_names)
6. **get_document_chunks** - Read entire document sequentially
(doc_id is not doc_name, do list documents to get the doc_id)

**Advanced Research Strategy:**
- **FOCUS FIRST**: Start with targeted, specific queries (3-5 terms max)
- **PROGRESSIVE SEARCH**: Use findings from first search to inform next queries
- **AVOID KITCHEN SINK**: Never use overly broad queries with 10+ terms
- **CONTEXT-DRIVEN**: Tailor queries based on document types and previous findings
- **STRATEGIC SEQUENCE**: Follow logical research progression (overview → details → specifics)
- Navigate through documents using context tools to build complete understanding
- Cross-reference information across multiple document sources
- Use sequential navigation to follow narrative threads and connect related information
- Synthesize findings from all sources into comprehensive, well-structured answers
- always check next chunk or previous chunk if the answer looks open ended

Currently researching: {self.entity_name}

**Response Quality Standards:**
- Provide specific details, numbers, dates, and names when available
- Structure information logically with clear sections
- **CRITICAL: Add inline citations using the format [[N](node_id)] where N is the citation number**
  Example: "The company's revenue grew 25% [[1](company123_doc_45678_3)]"
- Highlight key insights and critical findings
- Identify gaps in information and areas needing additional research
- Use multiple tool calls to gather comprehensive information before responding

**Citation Format:**
- **Every search result includes a 'node_id' field - USE THIS EXACT VALUE for citations**
- When you reference information from a chunk, immediately cite it with [[N](node_id)]
- N is a sequential number (1, 2, 3, ...)
- Simply copy the 'node_id' value from the search result JSON
- Example: If result has "node_id": "ent_123_doc_456_7", cite as [[1](ent_123_doc_456_7)]
- Example response: "Q4 revenue was $50M [[1](ent_123_doc_456_7)], an increase from last year [[2](ent_123_doc_456_8)]"

Use the tools strategically and extensively to provide thorough, well-researched, data-driven answers based on the company's documents."""

    def get_tools(self) -> List[Dict[str, Any]]:
        """Get available RAG navigation tools for OpenAI function calling"""
        tools: List[Dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "semantic_search_within_entity",
                    "description": "Primary query tool to find relevant information within the company's documents. Use FOCUSED, targeted queries. Avoid overly broad queries Use progressive queries based on findings.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Focused search query. Use specific, targeted queries. Build progressive queries based on findings."
                            },
                            "k": {"type": "integer", "description": "Number of results to return (default: 25, max recommended: 25)", "default": 25},
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_previous_chunk",
                    "description": "Navigate to the previous chunk in the same document to read what comes before a current chunk. Essential for understanding context, background information, or the setup/introduction to current findings. Use when you need to see what led up to important information or when current chunk references previous content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string", "description": "Document ID from previous search result (doc_id is not doc name do list documents to get the doc_id) (format: doc_xxxxx)"},
                            "chunk_order_index": {"type": "integer", "description": "Current chunk index from previous search result - will return chunk at index-1"},
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": ["doc_id", "chunk_order_index"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_next_chunk",
                    "description": "Navigate to the next chunk in the same document to read continuation of information. Critical for following narrative threads, getting complete stories, reading conclusions, or understanding outcomes. Use when current chunk seems to continue in next section or when you need to see results/conclusions of discussed topics.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string", "description": "Document ID from previous search result  (doc_id is not doc name do list documents to get the doc_id) (format: doc_xxxxx)"},
                            "chunk_order_index": {"type": "integer", "description": "Current chunk index from previous search result - will return chunk at index+1"},
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": ["doc_id", "chunk_order_index"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_chunk_context",
                    "description": "Get surrounding context chunks (previous + current + next) around a specific chunk to understand the complete narrative flow. Essential for getting full understanding of complex topics, complete financial data presentations, or comprehensive business discussions. Use context_size=2 for broader context on important findings.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string", "description": "Document ID from previous search result (doc_id is not doc name do list documents to get the doc_id) (format: doc_xxxxx)"},
                            "chunk_order_index": {"type": "integer", "description": "Target chunk index from previous search result - will return surrounding chunks"},
                            "context_size": {"type": "integer", "description": "Number of chunks before/after to include (1=±1 chunk, 2=±2 chunks for broader context). Use 2 for complex topics.", "default": 1},
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": ["doc_id", "chunk_order_index"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_entity_documents",
                    "description": "List all documents available for the company to understand what information sources exist. Use this strategically at the beginning of research to identify document types (pitch decks, financial statements, legal docs, etc.) and plan your search strategy. Helps determine what types of information are available and where to focus detailed searches.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_document_chunks",
                    "description": "Get all chunks of a specific document in sequential order for comprehensive document review. Best used for financial statements, pitch decks, legal documents, or other structured documents where you need to understand the complete flow and all sections. Returns first 10 chunks with navigation hints for longer documents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string", "description": "Document ID to read sequentially (doc_id is not doc name do list documents to get the doc_id) (format: doc_xxxxx from previous searches or document list)"},
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": ["doc_id"]
                    }
                }
            }
        ]

        return tools

    async def execute_function(self, func_name: str, arguments: Dict[str, Any], node_ids: List[str], relationship_ids: List[Dict[str, str]], cumulative_services: Optional[List[Service]]=None):
        """Execute the requested RAG navigation function and track nodes/relationships/services"""
        if cumulative_services is None:
            cumulative_services = []

        try:
            # Check if entity-scoped or global RAG is available
            if not (self.use_entity_scoped or self.rag_system):
                return "RAG system not available", node_ids, relationship_ids, cumulative_services

            if func_name == "semantic_search_within_entity":
                query = arguments["query"]
                k = arguments.get("k", 5)

                if self.use_entity_scoped:
                    # Use entity-scoped search (much faster!) with service tracking
                    results_docs, search_services = self.entity_store.search(query, k=k)
                    logger.debug(f"Semantic search services: {search_services}")
                    cumulative_services.extend(search_services)

                    # Convert to expected format
                    results = []
                    for doc in results_docs:
                        metadata = doc.metadata
                        doc_id = metadata.get('metadata', {}).get('doc_id')
                        source = metadata.get('chunk', {}).get('source')
                        chunk_index = metadata.get('chunk', {}).get('chunk_order_index')

                        if doc_id is not None and chunk_index is not None:
                            # Track node: format is {entity_id}_{doc_id}_{chunk_order_index}
                            node_id = f"{self.id}_{doc_id}_{chunk_index}"
                            if node_id not in node_ids:
                                node_ids.append(node_id)

                            results.append({
                                'content': doc.page_content,
                                'doc_id': doc_id,
                                'chunk_order_index': chunk_index,
                                'source': source,
                                'can_navigate': True,
                                'entity_id': self.id,  # For citation construction
                                'node_id': node_id  # Full node_id for easy reference
                            })
                else:
                    # Use global RAG (legacy)
                    results = self.rag_system.semantic_search_within_entity(query, self.id, k)

                    # Track nodes from global RAG results and add entity_id
                    for result in results:
                        doc_id = result.get('doc_id')
                        chunk_index = result.get('chunk_order_index')
                        if doc_id is not None and chunk_index is not None:
                            node_id = f"{self.id}_{doc_id}_{chunk_index}"
                            if node_id not in node_ids:
                                node_ids.append(node_id)
                            # Add entity_id and node_id to result
                            result['entity_id'] = self.id
                            result['node_id'] = node_id

                if not results:
                    return f"No results found for query '{query}' in documents.", node_ids, relationship_ids, cumulative_services
                return json.dumps(results), node_ids, relationship_ids, cumulative_services

            elif func_name == "get_previous_chunk":
                doc_id = arguments["doc_id"]
                chunk_idx = arguments["chunk_order_index"]

                # These navigation functions use global RAG (from storage)
                # Entity-scoped RAG focuses on search performance
                if self.use_entity_scoped:
                    # Fall back to global RAG for navigation
                    result = self.entity_store.get_previous_chunk(doc_id, chunk_idx)
                else:
                    result = self.rag_system.get_previous_chunk(doc_id, chunk_idx)

                if result:
                    # Track the previous chunk node
                    prev_chunk_idx = chunk_idx - 1
                    prev_node_id = f"{self.id}_{doc_id}_{prev_chunk_idx}"
                    if prev_node_id not in node_ids:
                        node_ids.append(prev_node_id)

                    # Add entity_id and node_id to result for citation
                    result['entity_id'] = self.id
                    result['node_id'] = prev_node_id

                    # Track relationship: from current to previous
                    current_node_id = f"{self.id}_{doc_id}_{chunk_idx}"
                    relationship = {"source_id": current_node_id, "target_id": prev_node_id}
                    if relationship not in relationship_ids:
                        relationship_ids.append(relationship)

                    return json.dumps(result), node_ids, relationship_ids, cumulative_services
                else:
                    return f"No previous chunk found for {doc_id}:{chunk_idx}", node_ids, relationship_ids, cumulative_services

            elif func_name == "get_next_chunk":
                doc_id = arguments["doc_id"]
                chunk_idx = arguments["chunk_order_index"]

                if self.use_entity_scoped:
                    result = self.entity_store.get_next_chunk(doc_id, chunk_idx)
                else:
                    result = self.rag_system.get_next_chunk(doc_id, chunk_idx)

                if result:
                    # Track the next chunk node
                    next_chunk_idx = chunk_idx + 1
                    next_node_id = f"{self.id}_{doc_id}_{next_chunk_idx}"
                    if next_node_id not in node_ids:
                        node_ids.append(next_node_id)

                    # Add entity_id and node_id to result for citation
                    result['entity_id'] = self.id
                    result['node_id'] = next_node_id

                    # Track relationship: from current to next
                    current_node_id = f"{self.id}_{doc_id}_{chunk_idx}"
                    relationship = {"source_id": current_node_id, "target_id": next_node_id}
                    if relationship not in relationship_ids:
                        relationship_ids.append(relationship)

                    return json.dumps(result), node_ids, relationship_ids, cumulative_services
                else:
                    return f"No next chunk found for {doc_id}:{chunk_idx}", node_ids, relationship_ids, cumulative_services

            elif func_name == "get_chunk_context":
                doc_id = arguments["doc_id"]
                chunk_idx = arguments["chunk_order_index"]
                context_size = arguments.get("context_size", 1)

                if self.use_entity_scoped:
                    result = self.entity_store.get_chunk_context(doc_id, chunk_idx, context_size)
                else:
                    result = self.rag_system.get_chunk_context(doc_id, chunk_idx, context_size)

                # Track nodes and relationships for context chunks
                current_node_id = f"{self.id}_{doc_id}_{chunk_idx}"

                # Add entity_id and node_id to current chunk
                if result.get('current'):
                    result['current']['entity_id'] = self.id
                    result['current']['node_id'] = current_node_id
                    if current_node_id not in node_ids:
                        node_ids.append(current_node_id)

                # Add entity_id and node_id to before chunks
                for i, before_chunk in enumerate(result.get('before', [])):
                    before_idx = chunk_idx - context_size + i
                    before_node_id = f"{self.id}_{doc_id}_{before_idx}"
                    before_chunk['entity_id'] = self.id
                    before_chunk['node_id'] = before_node_id

                    if before_node_id not in node_ids:
                        node_ids.append(before_node_id)

                    relationship = {"source_id": current_node_id, "target_id": before_node_id}
                    if relationship not in relationship_ids:
                        relationship_ids.append(relationship)

                # Add entity_id and node_id to after chunks
                for i, after_chunk in enumerate(result.get('after', [])):
                    after_idx = chunk_idx + i + 1
                    after_node_id = f"{self.id}_{doc_id}_{after_idx}"
                    after_chunk['entity_id'] = self.id
                    after_chunk['node_id'] = after_node_id

                    if after_node_id not in node_ids:
                        node_ids.append(after_node_id)

                    relationship = {"source_id": current_node_id, "target_id": after_node_id}
                    if relationship not in relationship_ids:
                        relationship_ids.append(relationship)

                response = f"**Context around {doc_id}:{chunk_idx}:\n{json.dumps(result, indent=1)}**\n\n"

                return response, node_ids, relationship_ids, cumulative_services

            elif func_name == "get_entity_documents":
                if self.use_entity_scoped:
                    results = self.entity_store.get_entity_documents()
                else:
                    results = self.rag_system.get_entity_documents(self.id)

                if not results:
                    return f"No documents found for {self.entity_name}", node_ids, relationship_ids, cumulative_services

                response = f"**Available documents for {self.entity_name}:**\n\n"
                for i, doc in enumerate(results, 1):
                    doc_id = doc.get('doc_id', 'unknown')
                    doc_name = doc.get('doc_name', 'unknown')
                    response += f"{i}. **{doc_name}** (ID: {doc_id})\n"

                return response, node_ids, relationship_ids, cumulative_services

            elif func_name == "get_document_chunks":
                doc_id = arguments["doc_id"]

                if self.use_entity_scoped:
                    results = self.entity_store.get_document_chunks_in_order(doc_id)
                else:
                    results = self.rag_system.get_document_chunks_in_order(doc_id)

                if not results:
                    return f"No chunks found for document {doc_id}", node_ids, relationship_ids, cumulative_services

                response = f"**All chunks for document {doc_id}:**\n\n"

                # Track nodes for all chunks in the document (limited to first 10)
                for i, chunk in enumerate(results[:10]):
                    content = chunk.get('chunk', {}).get('text', '')
                    idx = chunk.get('chunk', {}).get('chunk_order_index', 'unknown')

                    # Track node
                    node_id = f"{self.id}_{doc_id}_{idx}"
                    if node_id not in node_ids:
                        node_ids.append(node_id)

                    # Track sequential relationships between chunks
                    if i > 0:
                        prev_idx = results[i-1].get('chunk', {}).get('chunk_order_index', 'unknown')
                        prev_node_id = f"{self.id}_{doc_id}_{prev_idx}"
                        relationship = {"source_id": prev_node_id, "target_id": node_id}
                        if relationship not in relationship_ids:
                            relationship_ids.append(relationship)

                    response += f"**Chunk {idx}:** {content[:200]}{'...' if len(content) > 200 else ''}\n\n"

                if len(results) > 10:
                    response += f"... and {len(results) - 10} more chunks. Use navigation tools to explore specific sections."

                return response, node_ids, relationship_ids, cumulative_services

            else:
                return "I'm not sure how to help with that specific request.", node_ids, relationship_ids, cumulative_services

        except Exception as e:
            logger.error(f"Error executing function {func_name}: {e}")
            return f"I encountered an issue while processing your request: {str(e)}", node_ids, relationship_ids, cumulative_services

# Helper function for easy usage
async def research_company_question(company_id: str, company_name: str, question: str):
    """
    Convenience function to research a company question using the intelligent agent.

    Args:
        company_id: The company entity ID to research
        question: The question to research about the company

    Returns:
        Comprehensive research answer based on company documents
    """
    agent = ResearchAgent(company_id, company_name)
    async for response in agent.research_question(ResponseRequiredRequest(
        interaction_type="response_required",
        response_id=1,
        transcript=[{"role": "user", "content": question}]
    ), None):
        yield response.content  # Adjust this to handle the response as needed
