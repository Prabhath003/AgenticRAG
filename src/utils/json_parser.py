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
Robust JSON Parser for LLM Outputs
==================================

Handles various malformed JSON issues from LLMs:
- Comments (// # /* */)
- Missing quotes on keys and values
- Missing brackets, braces
- Trailing commas
- JavaScript object syntax
- Unbalanced structures

"""
# src/utils/json_parser.py
import ast
import json
import re
import subprocess
import tempfile
import os
import threading
from typing import Dict, Any, Optional

from ..log_creator import get_file_logger

logger = get_file_logger()

# Thread-safe cache for parsed results
_json_cache: Dict[str, Dict[str, Any]] = {}
_json_cache_lock = threading.Lock()

def extract_json_from_llm_response(response_text: str) -> Dict[str, Any]:
    """
    Extract and parse JSON from LLM response using multiple robust strategies.
    
    This function handles various malformed JSON issues that LLMs commonly produce:
    - Comments in various formats (// # /* */)
    - Missing quotes on keys and values  
    - Unbalanced brackets, braces, quotes
    - Trailing commas
    - JavaScript object syntax
    - Mixed quote styles
    
    Args:
        response_text (str): Raw LLM response text containing JSON
        
    Returns:
        Dict[str, Any]: Parsed JSON object, empty dict if all parsing fails
        
    Example:
        >>> response = '''
        ... {
        ...     name: "John", // Comment here
        ...     age: 25,
        ...     items: [item1, item2]
        ... }
        ... '''
        >>> result = extract_json_from_llm_response(response)
        >>> print(result)
        {'name': 'John', 'age': 25, 'items': ['item1', 'item2']}
    """
    # Check cache first
    with _json_cache_lock:
        if response_text in _json_cache:
            return _json_cache[response_text]

    # Step 1: Clean markdown and find JSON boundaries
    cleaned_text = _remove_markdown_blocks(response_text)
    json_boundaries = _find_json_boundaries(cleaned_text)
    
    if not json_boundaries:
        logger.debug("No JSON object found in response")
        raise Exception("No JSON object found in response")

    raw_json = json_boundaries['content']
    
    # Step 2: Try multiple parsing strategies
    parsing_strategies = [
        ("Direct JSON Parse", _try_direct_json_parse),
        ("Python AST Parse", _try_python_ast_parse), 
        ("Node.js Parse", _try_nodejs_parse),
        ("Progressive Fix Parse", _try_progressive_fix_parse)
    ]
    
    for strategy_name, parse_function in parsing_strategies:
        try:
            result = parse_function(raw_json)
            if result is not None and isinstance(result, dict):
                logger.debug(f"Successfully parsed using: {strategy_name}")
                # Cache successful result
                with _json_cache_lock:
                    _json_cache[response_text] = result
                return result
        except Exception as e:
            logger.debug(f"{strategy_name} failed: {str(e)}")
            continue
    
    # All strategies failed
    logger.error(f"All parsing strategies failed for JSON: {raw_json[:200]}...")
    raise Exception(f"All parsing strategies failed for JSON: {raw_json[:200]}...")

def _remove_markdown_blocks(text: str) -> str:
    """Remove markdown code block markers."""
    # Remove ```json, ```javascript, ```js, ``` markers
    cleaned = re.sub(r'^```(?:json|javascript|js)?\s*\n?', '', text, flags=re.MULTILINE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def _find_json_boundaries(text: str) -> Optional[Dict[str, Any]]:
    """
    Find the boundaries of a JSON object in text using brace matching.
    
    Returns:
        Dict containing 'start', 'end', 'content' or None if not found
    """
    start_pos = text.find('{')
    if start_pos == -1:
        # Check if the entire content is just a quoted string (common LLM error)
        text = text.strip()
        if text.startswith('"') and text.endswith('"') and len(text) > 2:
            logger.debug(f"Found quoted string instead of JSON object: {text}")
            return None
        return None
    
    # Pre-clean to handle obvious issues that break brace matching
    pre_cleaned = _pre_clean_for_brace_matching(text[start_pos:])
    
    # Find matching closing brace
    in_string = False
    escape_next = False
    brace_count = 0
    
    for i, char in enumerate(pre_cleaned):
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
            
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return {
                        'start': start_pos,
                        'end': start_pos + i + 1,
                        'content': pre_cleaned[:i + 1]
                    }
    
    return None


def _pre_clean_for_brace_matching(json_text: str) -> str:
    """
    Pre-clean JSON text to fix issues that would break brace matching.
    This is a light cleaning pass to ensure we can find object boundaries.
    """
    lines = json_text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        # Remove obvious comments that could interfere with string detection
        cleaned_line = _remove_line_comments(line)
        
        # Fix obvious unclosed quotes that would break brace counting
        if cleaned_line.count('"') % 2 != 0:
            cleaned_line = _fix_obvious_quote_issues(cleaned_line)
            
        cleaned_lines.append(cleaned_line)
    
    return '\n'.join(cleaned_lines)


def _remove_line_comments(line: str) -> str:
    """Remove comments from a line while preserving strings."""
    result_chars = []
    in_string = False
    escape_next = False
    i = 0
    
    while i < len(line):
        char = line[i]
        
        if escape_next:
            result_chars.append(char)
            escape_next = False
            i += 1
            continue
            
        if char == '\\':
            escape_next = True
            result_chars.append(char)
            i += 1
            continue
            
        if char == '"':
            in_string = not in_string
            result_chars.append(char)
            i += 1
            continue
            
        if not in_string:
            # Check for comment patterns
            if char == '/' and i + 1 < len(line) and line[i + 1] == '/':
                break  # Rest of line is comment
            elif char == '#':
                break  # Rest of line is comment  
            elif char == '/' and i + 1 < len(line) and line[i + 1] == '*':
                # Block comment - skip until */
                i += 2
                while i + 1 < len(line):
                    if line[i] == '*' and line[i + 1] == '/':
                        i += 2
                        break
                    i += 1
                continue
        
        result_chars.append(char)
        i += 1
    
    return ''.join(result_chars).rstrip()


def _fix_obvious_quote_issues(line: str) -> str:
    """Fix obvious quote issues in a line."""
    # If line has odd number of quotes, try to fix
    quotes = [i for i, c in enumerate(line) if c == '"']
    
    if len(quotes) % 2 == 0:
        return line
        
    # Find the last quote and see if we need to close it
    last_quote = quotes[-1]
    after_quote = line[last_quote + 1:].strip()
    
    # If there's content after that should be quoted, add closing quote
    if after_quote and not after_quote.startswith((',', '}', ']')):
        # Look for natural break point
        break_point = None
        for i, char in enumerate(after_quote):
            if char in ',}]':
                break_point = i
                break
                
        if break_point is not None:
            pos = last_quote + 1 + break_point
            return line[:pos] + '"' + line[pos:]
        else:
            return line + '"'
    
    return line


def _try_direct_json_parse(json_str: str) -> Optional[Dict[str, Any]]:
    """Try parsing with standard json.loads()."""
    return json.loads(json_str)


def _try_python_ast_parse(json_str: str) -> Optional[Dict[str, Any]]:
    """Try parsing as Python dict using ast.literal_eval."""
    # Convert JavaScript/JSON literals to Python
    python_str = json_str.replace('true', 'True').replace('false', 'False').replace('null', 'None')
    result = ast.literal_eval(python_str)
    return result if isinstance(result, dict) else None


def _try_nodejs_parse(json_str: str) -> Optional[Dict[str, Any]]:
    """Try parsing using Node.js JavaScript engine."""
    try:
        # Create temporary JavaScript file
        js_code = f"""
        try {{
            const obj = {json_str};
            console.log(JSON.stringify(obj));
        }} catch (error) {{
            console.error('Parse error:', error.message);
            process.exit(1);
        }}
        """
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(js_code)
            temp_file = f.name
        
        try:
            # Execute with Node.js
            result = subprocess.run(
                ['node', temp_file], 
                capture_output=True, 
                text=True, 
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
                
        finally:
            # Clean up temp file
            if os.path.exists(temp_file):
                os.unlink(temp_file)
                
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    
    return None


def _try_progressive_fix_parse(json_str: str) -> Optional[Dict[str, Any]]:
    """Try parsing with progressive error fixing."""
    current_json = json_str
    max_attempts = 10  # Increased attempts
    
    # Apply fixes progressively - try after each fix
    fix_functions = [
        _comprehensive_comment_removal,
        _balance_all_brackets,  # Move bracket balancing earlier
        _fix_quotes_comprehensively, 
        _fix_comma_issues,
        _clean_extra_characters
    ]
    
    # Try parsing original first
    try:
        result = json.loads(current_json)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError as e:
        logger.debug(f"Original parsing failed: {e}")
    
    # Apply fixes one by one and test after each
    for i, fix_func in enumerate(fix_functions):
        try:
            current_json = fix_func(current_json)
            logger.debug(f"Applied fix {i+1}: {fix_func.__name__}")
            
            # Try parsing after this fix
            try:
                result = json.loads(current_json)
                if isinstance(result, dict):
                    logger.debug(f"Successfully parsed after fix {i+1}")
                    return result
            except json.JSONDecodeError as e:
                logger.debug(f"Still failing after fix {i+1}: {e}")
                continue
                
        except Exception as e:
            logger.debug(f"Fix function {fix_func.__name__} failed: {e}")
            continue
    
    # If still failing, try one final comprehensive fix
    try:
        final_json = _apply_all_fixes_at_once(json_str)
        result = json.loads(final_json)
        if isinstance(result, dict):
            return result
    except Exception as e:
        logger.debug(f"Final comprehensive fix failed: {e}")
    
    return None


def _comprehensive_comment_removal(json_str: str) -> str:
    """Remove all types of comments comprehensively."""
    lines = json_str.split('\n')
    cleaned_lines = []
    
    for line in lines:
        cleaned_line = _remove_line_comments(line)
        cleaned_lines.append(cleaned_line)
    
    return '\n'.join(cleaned_lines)


def _fix_quotes_comprehensively(json_str: str) -> str:
    """Fix all quote-related issues comprehensively."""
    lines = json_str.split('\n')
    fixed_lines = []
    
    for line in lines:
        if not line.strip():
            fixed_lines.append(line)
            continue
        
        # Step 1: Fix unquoted object keys
        line = re.sub(r'(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', line)
        
        # Step 2: Fix single quotes to double quotes
        line = re.sub(r"'([^']*)'", r'"\1"', line)
        
        # Step 3: Fix unquoted values after colons
        line = re.sub(r':\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*([,\]}]|$)', r': "\1"\2', line)
        
        # Step 4: Fix unquoted array elements
        def fix_array_elements(match):
            array_content = match.group(1)
            if not array_content.strip():
                return '[]'
                
            # Split by comma and process each element
            elements = [elem.strip() for elem in array_content.split(',')]
            fixed_elements = []
            
            for elem in elements:
                if not elem:
                    continue
                # If it's an unquoted identifier, quote it
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', elem):
                    fixed_elements.append(f'"{elem}"')
                elif not (elem.startswith('"') and elem.endswith('"')):
                    # If it's not already quoted and not a number/boolean
                    if not re.match(r'^(?:true|false|null|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$', elem):
                        fixed_elements.append(f'"{elem}"')
                    else:
                        fixed_elements.append(elem)
                else:
                    fixed_elements.append(elem)
            
            return '[' + ', '.join(fixed_elements) + ']'
        
        line = re.sub(r'\[([^\[\]]*)\]', fix_array_elements, line)
        
        # Step 5: Fix missing closing quotes
        if ':' in line and line.count('"') % 2 != 0:
            line = _fix_unclosed_quotes_in_line(line)
        
        fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)


def _fix_unclosed_quotes_in_line(line: str) -> str:
    """Fix unclosed quotes in a single line."""
    quotes = [i for i, c in enumerate(line) if c == '"']
    
    if len(quotes) % 2 == 0:
        return line
        
    # Find the last quote and check context
    last_quote = quotes[-1]
    before_quote = line[:last_quote].rstrip()
    after_quote = line[last_quote + 1:].rstrip()
    
    # If it looks like an opening quote for a value
    if before_quote.endswith(':'):
        # Add closing quote before terminators or at end
        terminator_match = re.search(r'([,\]}])', after_quote)
        if terminator_match:
            pos = last_quote + 1 + terminator_match.start()
            return line[:pos] + '"' + line[pos:]
        else:
            return line.rstrip() + '"'
    
    return line


def _balance_all_brackets(json_str: str) -> str:
    """Balance all types of brackets and braces."""
    logger.debug(f"Balancing brackets for: {json_str[:100]}...")
    
    # Count brackets while respecting strings
    in_string = False
    escape_next = False
    
    brace_count = 0      # {}
    bracket_count = 0    # []
    
    for char in json_str:
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        if char == '"':
            in_string = not in_string
            continue
            
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
            elif char == '[':
                bracket_count += 1
            elif char == ']':
                bracket_count -= 1
    
    logger.debug(f"Bracket counts - braces: {brace_count}, brackets: {bracket_count}")
    
    # Add missing closing brackets/braces
    if bracket_count > 0:
        json_str += ']' * bracket_count
        logger.debug(f"Added {bracket_count} closing brackets")
    elif bracket_count < 0:
        json_str = '[' * abs(bracket_count) + json_str
        logger.debug(f"Added {abs(bracket_count)} opening brackets")
        
    if brace_count > 0:
        json_str += '}' * brace_count
        logger.debug(f"Added {brace_count} closing braces")
    elif brace_count < 0:
        json_str = '{' * abs(brace_count) + json_str
        logger.debug(f"Added {abs(brace_count)} opening braces")
    
    return json_str


def _apply_all_fixes_at_once(json_str: str) -> str:
    """Apply all fixes comprehensively in one pass."""
    logger.debug("Applying all fixes at once")
    
    # Apply all fixes in sequence
    result = json_str
    result = _comprehensive_comment_removal(result)
    result = _balance_all_brackets(result)
    result = _fix_quotes_comprehensively(result)
    result = _fix_comma_issues(result)
    result = _clean_extra_characters(result)
    
    return result


def _fix_comma_issues(json_str: str) -> str:
    """Fix missing and trailing comma issues."""
    lines = json_str.split('\n')
    
    # Step 1: Add missing commas between elements
    for i in range(len(lines) - 1):
        current_line = lines[i].rstrip()
        next_line = lines[i + 1].lstrip()
        
        if not current_line or not next_line:
            continue
            
        # Check if we need a comma between lines
        current_ends_element = current_line.endswith(('"', '}', ']')) and not current_line.endswith((',', '{', '['))
        next_starts_element = next_line.startswith(('"', '{', '['))
        next_ends_structure = next_line.lstrip().startswith(('}', ']'))
        
        if current_ends_element and next_starts_element and not next_ends_structure:
            lines[i] = current_line + ','
    
    # Step 2: Remove trailing commas before closing brackets/braces
    json_str = '\n'.join(lines)
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
    
    return json_str


def _clean_extra_characters(json_str: str) -> str:
    """Clean up any remaining problematic characters."""
    # Remove any stray characters that might cause issues
    # This is a conservative cleanup
    
    # Remove multiple consecutive commas
    json_str = re.sub(r',+', ',', json_str)
    
    # Remove commas at start of lines (malformed)
    json_str = re.sub(r'\n\s*,', ',', json_str)
    
    # Clean up whitespace
    lines = json_str.split('\n')
    cleaned_lines = [line.rstrip() for line in lines]
    
    return '\n'.join(cleaned_lines)


# Test and example usage
if __name__ == "__main__":
    
    # Test cases covering various malformed JSON scenarios
    test_cases = [
        # Test 1: Comments
        '''
        {
            "name": "test", // This is a comment
            "value": 42 # Python style comment
        }
        ''',
        
        # Test 2: Unquoted keys and values
        '''
        {
            name: "test",
            value: unquoted_value,
            items: [item1, item2]
        }
        ''',
        
        # Test 3: Missing closing bracket
        '''
        {
            "data": ["item1", "item2"
        }
        ''',
        
        # Test 4: The original problematic case
        '''
        {
            "water": "Life", // This json is about water and life
            "sunlight" : "photosynthesis,
            "hi" : {
                "hi" : {
                    "hi": ["1"] // this is a list
                }
            }
        }
        ''',
        
        # Test 5: Mixed quote styles and trailing commas
        '''
        {
            'name': "John",
            "age": 25,
            "hobbies": ['reading', "writing",],
        }
        '''
    ]
    
    print("=== LLM JSON Parser Test Results ===\n")
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"Test Case {i}:")
        print("Input:", repr(test_case.strip()[:100]) + "...")
        
        try:
            result = extract_json_from_llm_response(test_case)
            if result:
                print("✅ SUCCESS")
                print("Output:", result)
            else:
                print("❌ FAILED - returned empty dict")
        except Exception as e:
            print(f"❌ FAILED - exception: {e}")
        
        print("-" * 50)
    
    print("\n=== Summary ===")
    print("Parser handles:")
    print("• Comments (// # /* */)")
    print("• Unquoted keys and values") 
    print("• Missing brackets and braces")
    print("• Trailing commas")
    print("• Mixed quote styles")
    print("• JavaScript object syntax")
    print("• Progressive error recovery")