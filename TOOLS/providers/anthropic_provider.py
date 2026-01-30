from __future__ import annotations
import os
import requests
from typing import Dict, Any
from .base import BaseProvider, LLMResponse, ProviderError


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is missing.")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    def chat(self, system: str, user: str, **kwargs) -> LLMResponse:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", 1200),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code >= 400:
            raise ProviderError(f"Anthropic error {r.status_code}: {r.text}")

        data = r.json()
        text = ""
        # Anthropic: content is list of blocks: [{"type":"text","text":"..."}]
        content = data.get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "") or ""

        if not text:
            raise ProviderError("Anthropic response contained no text.")
        return LLMResponse(text=text, raw=data)
