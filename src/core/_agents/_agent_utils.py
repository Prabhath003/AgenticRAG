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

"""
Agent utility functions organized by category for token management, cost calculation,
and content processing.

These are standalone utility functions extracted from MainAgent to improve modularity
and allow reuse in other contexts.

Categories:
  - Token & Cost Management: Model pricing, token counting, cost calculation
  - Content Processing: Citation parsing, XML tag handling, content block detection
"""

from typing import List, Dict, Any, Tuple, Optional, Literal
import re
import json
import tiktoken

from ...log_creator import get_file_logger

logger = get_file_logger()


# =============================================================================
# CATEGORY: Token & Cost Management
# =============================================================================
# Functions for managing tokens, model pricing, and cost calculations


def sync_get_model_pricing(model_name: str) -> Tuple[float, float, float]:
    """
    Get input, output, and cache token pricing for different OpenAI models.

    Args:
        model_name: Name of the model (e.g., 'gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini')

    Returns:
        Tuple of (input_price_per_1M_tokens, output_price_per_1M_tokens, cache_read_price_per_1M_tokens)
    """
    # Normalize model name for comparison
    model_lower = model_name.lower()

    # Pricing dictionary for different model families
    pricing_map: Dict[str, Tuple[float, float, float]] = {
        # GPT-4o family
        "gpt-4o": (2.5, 10.0, 1.25),
        # GPT-4.1 family
        "gpt-4.1": (2.0, 8, 0.5),
        # GPT-4o-mini family (cheapest)
        "gpt-4o-mini": (0.15, 0.60, 0.075),
        "gpt-4.1-mini": (0.4, 1.6, 0.1),
        # Speculative future models
        "gpt-5-mini": (0.25, 2, 0.025),
    }

    # Check for exact match first
    if model_lower in pricing_map:
        return pricing_map[model_lower]

    # Check for partial matches
    for model_key, pricing in pricing_map.items():
        if model_key in model_lower:
            logger.debug(f"Using pricing for '{model_key}' based on model name '{model_name}'")
            return pricing

    # Default to gpt-4o pricing if model not recognized
    logger.warning(
        f"Model '{model_name}' not found in pricing map. Using gpt-4o pricing as fallback."
    )
    return (5.0, 15.0, 0.5)


def count_tokens_sync(text: str) -> int:
    """
    Count tokens in text using tiktoken for accurate token calculation.

    Args:
        text: Text to count tokens for

    Returns:
        Token count
    """
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text)
        return len(tokens)
    except Exception as e:
        logger.error(f"Error counting tokens with tiktoken: {e}")
        # Fallback: rough estimate (1 token ≈ 4 characters)
        return max(1, len(text) // 4)


def estimate_message_tokens_sync(
    messages: List[Dict[str, Any]],
) -> int:
    """
    Estimate total token count for a list of messages including tool_calls.

    Args:
        messages: List of message dictionaries

    Returns:
        Estimated total token count
    """
    try:
        total_tokens = 0

        # Count tokens for messages including tool_calls
        for msg in messages:
            # Count tokens for role
            role = msg.get("role", "")
            total_tokens += count_tokens_sync(role)

            # Count tokens for content
            content = msg.get("content", "")
            if isinstance(content, str):
                total_tokens += count_tokens_sync(content)
            elif isinstance(content, list):
                for item in content:  # type: ignore
                    if isinstance(item, dict):
                        text = str(item.get("text", ""))  # type: ignore
                        total_tokens += count_tokens_sync(text)

            # Count tokens for tool_calls if present
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tool_calls_json = json.dumps(tool_calls, indent=2)
                total_tokens += count_tokens_sync(tool_calls_json)

        return total_tokens
    except Exception as e:
        logger.error(f"Error estimating message tokens: {e}")
        return 0


def calculate_openai_cost_sync(
    input_tokens: int, output_tokens: int, cache_tokens: int, model_name: str
) -> float:
    """
    Calculate OpenAI API cost based on model and token usage, including cached tokens.

    Pricing varies by model (gpt-4o, gpt-4o-mini, gpt-4.1-mini, etc.)

    Cache tokens are charged at different rates from OpenAI:
    - Cache creation: same price as regular input tokens
    - Cache read: 90% discount (10% of regular input price)

    Args:
        input_tokens: Number of regular input tokens (not cached)
        output_tokens: Number of output tokens
        cache_tokens: Tokens read from cache
        model_name: Name of the model for pricing lookup

    Returns:
        Total cost in USD, rounded to 6 decimal places
    """
    # Pricing map for different models
    PRICING_MAP: Dict[str, tuple[float, float, float]] = {
        "gpt-4o": (5.0, 15.0, 0.5),
        "gpt-4o-mini": (0.15, 0.6, 0.075),
        "gpt-4-turbo": (10.0, 30.0, 5.0),
        "gpt-4": (30.0, 60.0, 15.0),
        "gpt-3.5-turbo": (0.5, 1.5, 0.25),
    }

    input_token_price, output_token_price, cache_token_price = PRICING_MAP.get(
        model_name, (5.0, 15.0, 0.5)  # Default to gpt-4o pricing
    )

    # Regular input token cost
    input_cost = (input_tokens / 1_000_000) * input_token_price

    # Cache token cost
    cache_cost = (cache_tokens / 1_000_000) * cache_token_price

    # Output token cost
    output_cost = (output_tokens / 1_000_000) * output_token_price

    total_cost = input_cost + cache_cost + output_cost

    return round(total_cost, 6)


# =============================================================================
# CATEGORY: Content Processing
# =============================================================================
# Functions for parsing citations, detecting content blocks, and handling XML


def parse_citations_from_content_sync(
    content: str,
) -> tuple[str, List[str], List[Dict[str, Any]]]:
    """
    Parse inline citations from content and return cleaned content + citations.

    Citation format: [N](node_id)
    Example: "Revenue grew 25% [1](company123_doc_45678_3)"

    Args:
        content: Content potentially containing citations

    Returns:
        Tuple of (content_with_citations, cited_node_ids, citations_list)
    """
    # Pattern to match [N](node_id)
    citation_pattern = r"\[(\d+)\]\(([^)]+)\)"

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
            parts = node_id.split("_")
            entity_id = parts[0] if len(parts) > 0 else "unknown"
            doc_id = parts[1] if len(parts) > 1 else "unknown"
            chunk_idx = parts[-1] if len(parts) > 2 else "unknown"

            citations.append(
                {
                    "citation_number": citation_num,
                    "node_id": node_id,
                    "entity_id": entity_id,
                    "doc_id": doc_id,
                    "chunk_index": chunk_idx,
                }
            )

    # Keep citations in content - they are visible to the user
    return content, cited_node_ids, citations


def parse_content_block_type_sync(
    accumulated_content: str,
) -> Optional[Literal["thought", "text"]]:
    """
    Parse the current position in accumulated content and determine the block type.

    Handles multiple alternating <thinking> and <answer> blocks.
    Returns the type of block we're currently inside, or None if not inside any tags.

    Args:
        accumulated_content: All accumulated content so far

    Returns:
        "thought" if inside <thinking> tag, "text" if inside <answer> tag, else None
    """
    # Find all tag positions (with optional surrounding newlines)
    thinking_opens = [m.start() for m in re.finditer(r"\n?<thinking>\n?", accumulated_content)]
    thinking_closes = [m.start() for m in re.finditer(r"\n?</thinking>\n?", accumulated_content)]
    answer_opens = [m.start() for m in re.finditer(r"\n?<answer>\n?", accumulated_content)]
    answer_closes = [m.start() for m in re.finditer(r"\n?</answer>\n?", accumulated_content)]

    current_pos = len(accumulated_content)

    # Count open tags up to current position
    thinking_open_count = sum(1 for pos in thinking_opens if pos < current_pos)
    thinking_close_count = sum(1 for pos in thinking_closes if pos < current_pos)
    answer_open_count = sum(1 for pos in answer_opens if pos < current_pos)
    answer_close_count = sum(1 for pos in answer_closes if pos < current_pos)

    # Determine if we're inside thinking tags
    if thinking_open_count > thinking_close_count:
        return "thought"

    # Determine if we're inside answer tags
    if answer_open_count > answer_close_count:
        return "text"

    # Return None if not inside any tags
    return None


def strip_xml_tags_sync(content: str) -> str:
    """
    Remove XML tags from content while preserving the actual text.

    Strips <thinking>, </thinking>, <answer>, </answer> tags along with surrounding newlines.

    Args:
        content: Content potentially containing XML tags

    Returns:
        Content with XML tags removed
    """
    # Remove the XML tags along with surrounding newlines
    content = re.sub(r"\n?<thinking>\n?", "", content)
    content = re.sub(r"\n?</thinking>\n?", "", content)
    content = re.sub(r"\n?<answer>\n?", "", content)
    content = re.sub(r"\n?</answer>\n?", "", content)
    return content
