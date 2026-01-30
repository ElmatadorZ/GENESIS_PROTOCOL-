from __future__ import annotations
from .base import BaseProvider, ProviderError
from .openai_provider import OpenAIProvider
from .anthropic_provider import AnthropicProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider


def get_provider(name: str) -> BaseProvider:
    name = (name or "").strip().lower()
    if name == "openai":
        return OpenAIProvider()
    if name == "anthropic":
        return AnthropicProvider()
    if name == "gemini":
        return GeminiProvider()
    if name == "ollama":
        return OllamaProvider()
    raise ProviderError(f"Unknown provider: {name}")
