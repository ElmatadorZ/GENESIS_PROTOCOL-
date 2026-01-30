from __future__ import annotations
import os
import requests
from typing import Dict, Any
from .base import BaseProvider, LLMResponse, ProviderError


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, model: str | None = None):
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is missing.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    def chat(self, system: str, user: str, **kwargs) -> LLMResponse:
        # OpenAI Responses API (simple, provider-agnostic-ish)
        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code >= 400:
            raise ProviderError(f"OpenAI error {r.status_code}: {r.text}")

        data = r.json()

        # Try to extract text robustly:
        text = ""
        if isinstance(data, dict):
            # Newer Responses API: output[0].content[0].text
            out = data.get("output", [])
            if out and isinstance(out, list):
                content = out[0].get("content", [])
                if content and isinstance(content, list):
                    text = content[0].get("text", "") or ""
            # Fallback:
            text = text or data.get("text", "") or ""

        if not text:
            raise ProviderError("OpenAI response contained no text.")
        return LLMResponse(text=text, raw=data)
