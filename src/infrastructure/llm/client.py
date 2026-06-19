"""Factory that turns config into a concrete provider."""

from __future__ import annotations

from .base import LLMProvider

_PROVIDERS = {"openai", "anthropic", "ollama"}


def build_provider(provider: str, settings: dict) -> LLMProvider:
    """Instantiate the provider named ``provider`` with ``settings``.

    Imports are deferred so that, e.g., a user running Ollama (stdlib only)
    never imports the ``anthropic`` or ``openai`` SDK.
    """
    provider = provider.lower()
    if provider == "openai":
        from .providers.openai_provider import OpenAIProvider

        return OpenAIProvider(**settings)
    if provider == "anthropic":
        from .providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(**settings)
    if provider == "ollama":
        from .providers.ollama_provider import OllamaProvider

        return OllamaProvider(**settings)
    raise ValueError(
        f"Unknown LLM provider {provider!r}. Choose one of: {', '.join(sorted(_PROVIDERS))}."
    )
