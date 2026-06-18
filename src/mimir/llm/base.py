"""Provider-agnostic LLM types and interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Literal

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


class LLMProvider(ABC):
    """Minimal chat interface every backend implements."""

    @abstractmethod
    def chat(self, messages: list[Message], **kwargs) -> str:
        """Return the assistant's full reply for ``messages``."""

    def stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        """Yield reply chunks. Default falls back to a single non-streamed chunk."""
        yield self.chat(messages, **kwargs)

    def complete(self, prompt: str, system: str | None = None, **kwargs) -> str:
        """Convenience one-shot helper."""
        messages: list[Message] = []
        if system:
            messages.append(Message("system", system))
        messages.append(Message("user", prompt))
        return self.chat(messages, **kwargs)
