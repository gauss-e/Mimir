"""Pluggable LLM access layer.

A single :class:`~mimir.llm.base.LLMProvider` interface backed by OpenAI,
Anthropic, or a local Ollama model. Build one with :func:`build_provider`.
"""

from .base import LLMProvider, Message
from .client import build_provider

__all__ = ["LLMProvider", "Message", "build_provider"]
