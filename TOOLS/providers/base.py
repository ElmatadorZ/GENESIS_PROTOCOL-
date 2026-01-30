from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class LLMResponse:
    text: str
    raw: Optional[Dict[str, Any]] = None


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    """
    Provider interface: implement .chat(system, user) -> LLMResponse
    """
    name: str = "base"

    def chat(self, system: str, user: str, **kwargs) -> LLMResponse:
        raise NotImplementedError
