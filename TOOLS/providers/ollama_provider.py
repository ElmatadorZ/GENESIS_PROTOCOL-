from __future__ import annotations
import os
import requests
from typing import Dict, Any
from .base import BaseProvider, LLMResponse, ProviderError


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3.1")

    def chat(self, system: str, user: str, **kwargs) -> LLMResponse:
        url = f"{self.base_url.rstrip('/')}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }

        r = requests.post(url, json=payload, timeout=60)
        if r.status_code >= 400:
            raise ProviderError(f"Ollama error {r.status_code}: {r.text}")

        data = r.json()
        msg = data.get("message", {})
        text = msg.get("content", "") if isinstance(msg, dict) else ""
        if not text:
            raise ProviderError("Ollama response contained no text.")
        return LLMResponse(text=text, raw=data)
