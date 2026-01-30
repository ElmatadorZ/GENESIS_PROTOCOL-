from __future__ import annotations
import os
import requests
from typing import Dict, Any
from .base import BaseProvider, LLMResponse, ProviderError


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, model: str | None = None):
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY is missing.")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

    def chat(self, system: str, user: str, **kwargs) -> LLMResponse:
        # Google Generative Language API (Gemini)
        # Endpoint pattern:
        # https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=API_KEY
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        headers = {"content-type": "application/json"}

        payload: Dict[str, Any] = {
            "contents": [
                {"role": "user", "parts": [{"text": f"SYSTEM:\n{system}\n\nUSER:\n{user}"}]}
            ]
        }

        r = requests.post(url, params=params, headers=headers, json=payload, timeout=60)
        if r.status_code >= 400:
            raise ProviderError(f"Gemini error {r.status_code}: {r.text}")

        data = r.json()
        text = ""
        # Gemini: candidates[0].content.parts[0].text
        cands = data.get("candidates", [])
        if isinstance(cands, list) and cands:
            content = cands[0].get("content", {})
            parts = content.get("parts", [])
            if isinstance(parts, list) and parts:
                text = parts[0].get("text", "") or ""

        if not text:
            raise ProviderError("Gemini response contained no text.")
        return LLMResponse(text=text, raw=data)
