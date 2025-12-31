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

# src/core/agents/custom_types.py
from typing import Any, List, Optional, Literal, Union
from pydantic import BaseModel
from typing import Literal, Dict, Optional


# Retell -> Your Server Events
class Utterance(BaseModel):
    role: Literal["agent", "user", "system"]
    content: str


class PingPongRequest(BaseModel):
    interaction_type: Literal["ping_pong"]
    timestamp: int


class CallDetailsRequest(BaseModel):
    interaction_type: Literal["call_details"]
    call: dict


class UpdateOnlyRequest(BaseModel):
    interaction_type: Literal["update_only"]
    transcript: List[Utterance]


class ResponseRequiredRequest(BaseModel):
    interaction_type: Literal["reminder_required", "response_required"]
    response_id: int
    transcript: List[Dict[str, Any]]


CustomLlmRequest = Union[
    ResponseRequiredRequest | UpdateOnlyRequest | CallDetailsRequest | PingPongRequest
]


# Your Server -> Retell Events
class ConfigResponse(BaseModel):
    response_type: Literal["config"] = "config"
    config: Dict[str, bool] = {
        "auto_reconnect": bool,
        "call_details": bool,
    }


class PingPongResponse(BaseModel):
    response_type: Literal["ping_pong"] = "ping_pong"
    timestamp: int


class ResponseResponse(BaseModel):
    response_type: Literal["response", "update", "usage"] = "response"
    response_id: int
    content: Optional[str]
    content_complete: bool
    end_call: Optional[bool] = False
    transfer_number: Optional[str] = None
    node_ids: List[str] = []  # All nodes used as context
    relationship_ids: List[str] = []  # All relationships from navigation
    cited_node_ids: List[str] = []  # Nodes actually used in the answer
    citations: List[Dict[str, Any]] = []  # Detailed citation information
    services_used: List[Dict[str, Any]] = []  # Services used (OpenAI, transformer, etc.)
    estimated_cost_usd: float = 0.0  # Estimated cost for this response


CustomLlmResponse = Union[ConfigResponse | PingPongResponse | ResponseResponse]