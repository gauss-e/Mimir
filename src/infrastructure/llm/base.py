"""Provider-agnostic LLM types and interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Iterator, Literal

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``input_schema`` is JSON Schema for the args."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


# Called by the agent loop to run a tool: (name, args) -> textual result.
Dispatch = Callable[[str, dict], str]


class LLMProvider(ABC):
    """Minimal chat interface every backend implements."""

    # Whether this backend can drive the tool-using agent loop (run_tools).
    supports_tools: bool = False

    @abstractmethod
    def chat(self, messages: list[Message], **kwargs) -> str:
        """Return the assistant's full reply for ``messages``."""

    def stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        """Yield reply chunks. Default falls back to a single non-streamed chunk."""
        yield self.chat(messages, **kwargs)

    def run_tools(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        dispatch: Dispatch,
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict], None] | None = None,
        max_steps: int = 8,
    ) -> str:
        """Run the agentic tool loop: model → tool calls → results → repeat.

        Streams assistant text via ``on_text`` and announces each tool call via
        ``on_tool``. Returns the final assistant text. Backends that don't
        support tools should leave ``supports_tools`` False; callers fall back
        to plain ``stream``.
        """
        raise NotImplementedError("this provider does not support tools")

    def complete(self, prompt: str, system: str | None = None, **kwargs) -> str:
        """Convenience one-shot helper."""
        messages: list[Message] = []
        if system:
            messages.append(Message("system", system))
        messages.append(Message("user", prompt))
        return self.chat(messages, **kwargs)
