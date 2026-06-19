"""Pluggable LLM access layer.

A single :class:`~llm.base.LLMProvider` interface backed by OpenAI,
Anthropic, or a local Ollama model. Build one with :func:`build_provider`.
"""

from .base import Dispatch, LLMProvider, Message, ToolSpec
from .client import build_provider

__all__ = ["LLMProvider", "Message", "ToolSpec", "Dispatch", "build_provider"]
