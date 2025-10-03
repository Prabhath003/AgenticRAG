# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
# All rights reserved.
#
# Developed by: GiKA AI Team
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhath@gikagraph.ai
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

# src/core/agents/company_research_agent.py
from typing import List, Dict, Any
import json
from openai import AsyncOpenAI
from dataclasses import dataclass

from .custom_types import ResponseResponse, Utterance
from ...config import Config
from ...log_creator import get_file_logger
from ..rag_system import get_rag_system

logger = get_file_logger()

@dataclass
class ResponseRequiredRequest2:
    interaction_type: str  # e.g., "response_required"
    response_id: int
    transcript: List[Dict[str, Any]]

class CompanyResearchAgent:
    """Single inference company research agent using RAG navigation tools"""

    def __init__(self, company_id: str):
        self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
        self.company_id = company_id
        self.model_name = "gpt-4o-mini"
        self.conversation_history: List[Dict[str, Any]] = []

        # Get company info from database
        try:
            with get_db_session() as session:
                company = session['company'].find_one({"_id": company_id})
                self.company_name = company.get("company_information", {}).get("legal_entity_name", "Unknown Company") if company else "Unknown Company"
        except Exception as e:
            logger.error(f"Error fetching company info: {e}")
            self.company_name = "Unknown Company"

        self.rag_system = get_rag_system()
        self.last_processed_response_id = 0

        # Generate system prompt
        self.system_prompt = self._generate_system_prompt()
    
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
        messages = []
        for utterance in transcript:
            if utterance.role == "agent":
                messages.append({"role": "assistant", "content": utterance.content})
            else:
                messages.append({"role": "user", "content": utterance.content})
        return messages

    async def research_question(self, request: ResponseRequiredRequest2, func_result=None):
        """Draft response for voice conversation using OpenAI"""
            
        messages = request.transcript
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
                            
                            result = await self.execute_function(
                                func_call["func_name"], 
                                func_call["arguments"]
                            )
                            
                            if isinstance(result, dict):
                                if result.get("action") == "end_call":
                                    logger.info(f"Tool-based end call with message: {result['message']}")
                                    response = ResponseResponse(
                                        response_id=request.response_id,
                                        content=result["message"],
                                        content_complete=True,
                                        end_call=True,
                                    )
                                    messages = self.add_to_messages(messages, Utterance(role="agent", content=result["message"]))
                                    yield response
                                    return
                            
                            if parsed_arguments.get("message") and not isinstance(result, dict):
                                response = ResponseResponse(
                                    response_id=request.response_id,
                                    content=parsed_arguments["message"],
                                    content_complete=False,
                                    end_call=False,
                                )
                                messages = self.add_to_messages(messages, Utterance(role="agent", content=parsed_arguments["message"]))
                                yield response
                            
                            func_result_data = {
                                "id": func_call["id"],
                                "arguments": func_call["arguments"],
                                "func_name": func_call["func_name"],
                                "result": result,
                            }

                            async for response in self.research_question(ResponseRequiredRequest2(
                                    interaction_type="response_required",
                                    response_id=request.response_id,
                                    transcript=messages
                                ), func_result_data):
                                yield response
                            return
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"Error parsing function arguments: {func_arguments} - {e}")
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content="I had trouble processing that request. How else can I help you?",
                                content_complete=True,
                                end_call=False,
                            )
                            messages = self.add_to_messages(messages, Utterance(role="agent", content="I had trouble processing that request. How else can I help you?"))
                            yield response
                            return
                        except Exception as e:
                            logger.error(f"Error executing function {func_call['func_name']}: {e}", exc_info=True)
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content="I encountered an issue while processing your request. How else can I help you?",
                                content_complete=True,
                                end_call=False,
                            )
                            messages = self.add_to_messages(messages, Utterance(role="agent", content="I encountered an issue while processing your request. How else can I help you?"))
                            yield response
                            return
                    
                    elif choice.finish_reason == "stop":                        
                        response = ResponseResponse(
                            response_id=request.response_id,
                            content="",
                            content_complete=True,
                            end_call=False,
                        )
                        messages = self.add_to_messages(messages, Utterance(role="agent", content=content_buffer))
                        yield response

        except Exception as e:
            logger.error(f"Error in draft_response: {e}")
            response = ResponseResponse(
                response_id=request.response_id,
                content="I apologize, but I encountered an issue. How else can I help you with finding courses?",
                content_complete=True,
                end_call=False,
            )
            messages = self.add_to_messages(messages, Utterance(role="agent", content="I apologize, but I encountered an issue. How else can I help you?"))
            yield response

    def _generate_system_prompt(self) -> str:
        """Generate system prompt for company intelligence"""
        return f"""You are GiKA, an AI-powered business intelligence assistant specializing in comprehensive company research.

**Your Mission:** Research {self.company_name} thoroughly by strategically using multiple RAG navigation tools to gather detailed, accurate information from company documents.

**Available RAG Navigation Tools:**
1. **semantic_search_within_entity** - Primary search tool to find relevant chunks (returns doc_id:chunk_order_index)
2. **get_previous_chunk** - Navigate to previous chunk in same document
3. **get_next_chunk** - Navigate to next chunk in same document
4. **get_chunk_context** - Get surrounding chunks (previous + current + next)
5. **get_entity_documents** - List all available documents for the company
6. **get_document_chunks** - Read entire document sequentially

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

Currently researching: {self.company_name}

**Response Quality Standards:**
- Provide specific details, numbers, dates, and names when available
- Structure information logically with clear sections
- Cite document sources and locations where information was found
- Highlight key insights and critical findings
- Identify gaps in information and areas needing additional research
- Use multiple tool calls to gather comprehensive information before responding

Use the tools strategically and extensively to provide thorough, well-researched, data-driven answers based on the company's documents."""

    def get_tools(self) -> List[Dict[str, Any]]:
        """Get available RAG navigation tools for OpenAI function calling"""
        tools = [
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
                            "k": {"type": "integer", "description": "Number of results to return (default: 5, max recommended: 10)", "default": 5}
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
                            "doc_id": {"type": "string", "description": "Document ID from previous search result (format: doc_xxxxx)"},
                            "chunk_order_index": {"type": "integer", "description": "Current chunk index from previous search result - will return chunk at index-1"}
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
                            "doc_id": {"type": "string", "description": "Document ID from previous search result (format: doc_xxxxx)"},
                            "chunk_order_index": {"type": "integer", "description": "Current chunk index from previous search result - will return chunk at index+1"}
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
                            "doc_id": {"type": "string", "description": "Document ID from previous search result (format: doc_xxxxx)"},
                            "chunk_order_index": {"type": "integer", "description": "Target chunk index from previous search result - will return surrounding chunks"},
                            "context_size": {"type": "integer", "description": "Number of chunks before/after to include (1=±1 chunk, 2=±2 chunks for broader context). Use 2 for complex topics.", "default": 1}
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
                        "properties": {},
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
                            "doc_id": {"type": "string", "description": "Document ID to read sequentially (format: doc_xxxxx from previous searches or document list)"}
                        },
                        "required": ["doc_id"]
                    }
                }
            }
        ]

        return tools

    async def execute_function(self, func_name: str, arguments: Dict[str, Any]):
        """Execute the requested RAG navigation function"""
        try:
            if not self.rag_system:
                return "RAG system not available for this company."

            if func_name == "semantic_search_within_entity":
                query = arguments["query"]
                k = arguments.get("k", 5)
                results = self.rag_system.semantic_search_within_entity(query, self.company_id, k)

                if not results:
                    return f"No results found for query '{query}' in company documents."
                return json.dumps(results)

            elif func_name == "get_previous_chunk":
                doc_id = arguments["doc_id"]
                chunk_idx = arguments["chunk_order_index"]
                result = self.rag_system.get_previous_chunk(doc_id, chunk_idx)

                if result:
                    return json.dumps(result)
                else:
                    return f"No previous chunk found for {doc_id}:{chunk_idx}"

            elif func_name == "get_next_chunk":
                doc_id = arguments["doc_id"]
                chunk_idx = arguments["chunk_order_index"]
                result = self.rag_system.get_next_chunk(doc_id, chunk_idx)

                if result:
                    return json.dumps(result)
                else:
                    return f"No next chunk found for {doc_id}:{chunk_idx}"

            elif func_name == "get_chunk_context":
                doc_id = arguments["doc_id"]
                chunk_idx = arguments["chunk_order_index"]
                context_size = arguments.get("context_size", 1)
                result = self.rag_system.get_chunk_context(doc_id, chunk_idx, context_size)

                response = f"**Context around {doc_id}:{chunk_idx}:\n{json.dumps(result, indent=1)}**\n\n"

                return response

            elif func_name == "get_entity_documents":
                results = self.rag_system.get_entity_documents(self.company_id)

                if not results:
                    return f"No documents found for company {self.company_name}"

                response = f"**Available documents for {self.company_name}:**\n\n"
                for i, doc in enumerate(results, 1):
                    doc_id = doc.get('doc_id', 'unknown')
                    doc_name = doc.get('doc_name', 'unknown')
                    response += f"{i}. **{doc_name}** (ID: {doc_id})\n"

                return response

            elif func_name == "get_document_chunks":
                doc_id = arguments["doc_id"]
                results = self.rag_system.get_document_chunks_in_order(doc_id)

                if not results:
                    return f"No chunks found for document {doc_id}"

                response = f"**All chunks for document {doc_id}:**\n\n"
                for chunk in results[:10]:  # Limit to first 10 chunks
                    content = chunk.get('chunk', {}).get('content', '')
                    idx = chunk.get('chunk', {}).get('chunk_order_index', 'unknown')
                    response += f"**Chunk {idx}:** {content[:200]}{'...' if len(content) > 200 else ''}\n\n"

                if len(results) > 10:
                    response += f"... and {len(results) - 10} more chunks. Use navigation tools to explore specific sections."

                return response

            else:
                return "I'm not sure how to help with that specific request."

        except Exception as e:
            logger.error(f"Error executing function {func_name}: {e}")
            return f"I encountered an issue while processing your request: {str(e)}"

# Helper function for easy usage
async def research_company_question(company_id: str, question: str):
    """
    Convenience function to research a company question using the intelligent agent.

    Args:
        company_id: The company entity ID to research
        question: The question to research about the company

    Returns:
        Comprehensive research answer based on company documents
    """
    agent = CompanyResearchAgent(company_id)
    async for response in agent.research_question(ResponseRequiredRequest2(
        interaction_type="response_required",
        response_id=1,
        transcript=[{"role": "user", "content": question}]
    ), None):
        yield response.content  # Adjust this to handle the response as needed
