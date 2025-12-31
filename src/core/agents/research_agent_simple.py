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
from typing import List, Dict, Any, Optional, Callable
import json
from openai import AsyncOpenAI, AsyncAzureOpenAI

from .custom_types import ResponseResponse, Utterance, ResponseRequiredRequest
from ...config import Config
from ...log_creator import get_file_logger
from ..rag_system import get_rag_system
from ..entity_scoped_rag import get_entity_rag_manager

logger = get_file_logger()

class ResearchAgent:
    """Single inference research agent using RAG navigation tools"""

    def __init__(self, id: str, entity_name: str, use_entity_scoped: bool = True, on_stage_change_callback: Optional[Callable[..., Any]]=None):
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

        # Use entity-scoped RAG for better performance if enabled
        if use_entity_scoped:
            self.entity_rag_manager = get_entity_rag_manager()
            self.entity_store = self.entity_rag_manager.get_entity_store(id)
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

    async def research_question(self, request: ResponseRequiredRequest, func_result: Optional[Dict[str, Any]]=None, node_ids: Optional[List[str]]=None, relationship_ids: Optional[List[Dict[str, str]]]=None):
        """Draft response for voice conversation using OpenAI"""

        # Initialize tracking lists if not provided
        if node_ids is None:
            node_ids = []
        if relationship_ids is None:
            relationship_ids = []

        messages: List[Dict[str, Any]] = request.transcript
        try:
            # Add system message
            if messages:
                if messages[0]['role'] != 'system':
                    system_message = {"role": "system", "content": self.system_prompt}
                    messages = [system_message] + messages
            
            logger.debug(f"{json.dumps(messages, indent=2)}")
            
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

            async for chunk in stream:
                
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]
                    delta = choice.delta
                    
                    if delta.content:
                        content_buffer += delta.content
                        response = ResponseResponse(
                            response_id=request.response_id,
                            content=delta.content,
                            content_complete=False,
                            end_call=False,
                            node_ids=node_ids,
                            relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                            cited_node_ids=[],  # Will be populated at the end
                            citations=[],  # Will be populated at the end
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
                            
                            result, node_ids, relationship_ids = await self.execute_function(
                                func_call["func_name"],
                                func_call["arguments"],
                                node_ids,
                                relationship_ids
                            )
                            
                            if isinstance(result, dict):
                                if result.get("action") == "end_call":
                                    logger.info(f"Tool-based end call with message: {result['message']}")
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
                                    )
                                    # messages = self.add_to_messages(messages, Utterance(role="agent", content=result["message"]))
                                    yield response
                                    return
                            
                            if parsed_arguments.get("message") and not isinstance(result, dict):
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
                                )
                                # messages = self.add_to_messages(messages, Utterance(role="agent", content=parsed_arguments["message"]))
                                yield response
                            
                            func_result_data = {
                                "id": func_call["id"],
                                "arguments": func_call["arguments"],
                                "func_name": func_call["func_name"],
                                "result": result,
                            }

                            async for response in self.research_question(ResponseRequiredRequest(
                                    interaction_type="response_required",
                                    response_id=request.response_id,
                                    transcript=messages
                                ), func_result_data, node_ids, relationship_ids):
                                yield response
                            return
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"Error parsing function arguments: {func_arguments} - {e}")
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content="I had trouble processing that request. How else can I help you?",
                                content_complete=True,
                                end_call=False,
                                node_ids=node_ids,
                                relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                                cited_node_ids=[],
                                citations=[],
                            )
                            self.conversation_history = self.add_to_messages(self.conversation_history, Utterance(role="agent", content="I had trouble processing that request. How else can I help you?"))
                            yield response
                            return
                        except Exception as e:
                            logger.error(f"Error executing function {func_call['func_name']}: {e}", exc_info=True)
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content="I encountered an issue while processing your request. How else can I help you?",
                                content_complete=True,
                                end_call=False,
                                node_ids=node_ids,
                                relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                                cited_node_ids=[],
                                citations=[],
                            )
                            self.conversation_history = self.add_to_messages(self.conversation_history, Utterance(role="agent", content="I encountered an issue while processing your request. How else can I help you?"))
                            yield response
                            return
                    
                    elif choice.finish_reason == "stop":
                        # Parse citations from the complete response
                        _, cited_node_ids, citations = self.parse_citations_from_content(content_buffer)

                        response = ResponseResponse(
                            response_id=request.response_id,
                            content="",
                            content_complete=True,
                            end_call=False,
                            node_ids=node_ids,
                            relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                            cited_node_ids=cited_node_ids,
                            citations=citations,
                        )
                        self.conversation_history = self.add_to_messages(self.conversation_history, Utterance(role="agent", content=content_buffer))
                        yield response

        except Exception as e:
            logger.error(f"Error in draft_response: {e}")
            response = ResponseResponse(
                response_id=request.response_id,
                content="I apologize, but I encountered an issue. How else can I help you with finding courses?",
                content_complete=True,
                end_call=False,
                node_ids=node_ids,
                relationship_ids=[f"{rel['source_id']}:{rel['target_id']}" for rel in relationship_ids],
                cited_node_ids=[],
                citations=[],
            )
            self.conversation_history = self.add_to_messages(self.conversation_history, Utterance(role="agent", content="I apologize, but I encountered an issue. How else can I help you?"))
            yield response

    def _generate_system_prompt(self) -> str:
        """Generate simple system prompt - use semantic search once, then answer"""
        return f"""You are a research assistant for {self.entity_name}.

**Your workflow:**
1. First, call the semantic_search_within_entity tool ONCE to retrieve relevant context (top 20 results recommended)
2. Once you have the context, provide a comprehensive answer based on the retrieved information
3. Do NOT call any other tools or make multiple searches

**Response Guidelines:**
- Answer based on the context retrieved from the semantic search
- Be clear, specific, and comprehensive
- Include relevant details, numbers, and facts from the context
- If the context doesn't contain enough information, acknowledge it
- Structure your answer logically
- cite sources

**Citation Format:**
- **Every search result includes a 'node_id' field - USE THIS EXACT VALUE for citations**
- When you reference information from a chunk, immediately cite it with [[N](node_id)]
- N is a sequential number (1, 2, 3, ...)
- Simply copy the 'node_id' value from the search result JSON
- Example: If result has "node_id": "ent_123_doc_456_7", cite as [[1](ent_123_doc_456_7)]
- Example response: "Q4 revenue was $50M [[1](ent_123_doc_456_7)], an increase from last year [[2](ent_123_doc_456_8)]"
"""

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
                            "k": {"type": "integer", "description": "Number of results to return (default: 5, max: 10)", "default": 5},
                            "message": {
                                "type": "string",
                                "description": "message to user to hint about the ongoing analysis"
                            }
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

        return tools

    async def execute_function(self, func_name: str, arguments: Dict[str, Any], node_ids: List[str], relationship_ids: List[Dict[str, str]]):
        """Execute the requested RAG navigation function and track nodes/relationships"""
        try:
            # Check if entity-scoped or global RAG is available
            if not (self.use_entity_scoped or self.rag_system):
                return "RAG system not available", node_ids, relationship_ids

            if func_name == "semantic_search_within_entity":
                query = arguments["query"]
                k = arguments.get("k", 5)
                k = min(k, 10)

                if self.use_entity_scoped:
                    # Use entity-scoped search (much faster!)
                    results_docs = self.entity_store.search(query, k=k)

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
                    return f"No results found for query '{query}' in documents.", node_ids, relationship_ids
                return json.dumps(results), node_ids, relationship_ids

            else:
                return "I'm not sure how to help with that specific request.", node_ids, relationship_ids

        except Exception as e:
            logger.error(f"Error executing function {func_name}: {e}")
            return f"I encountered an issue while processing your request: {str(e)}", node_ids, relationship_ids

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
