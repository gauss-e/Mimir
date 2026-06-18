"""Anthropic (Claude) backend.

Anthropic keeps the system prompt out of the message list, so system messages
are concatenated and passed separately.
"""

from __future__ import annotations

from typing import Iterator

from ..base import LLMProvider, Message


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        api_key: str = "",
        max_tokens: int = 4096,
        **_,
    ):
        from anthropic import Anthropic

        if not api_key:
            raise ValueError(
                "Anthropic provider needs an api_key (or ANTHROPIC_API_KEY)."
            )
        self.model = model
        self.max_tokens = max_tokens
        self._client = Anthropic(api_key=api_key)

    def _split(self, messages: list[Message]) -> tuple[str, list[dict]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        return system, convo

    def chat(self, messages: list[Message], **kwargs) -> str:
        system, convo = self._split(messages)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            system=system or None,
            messages=convo,
            **kwargs,
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    def stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        system, convo = self._split(messages)
        with self._client.messages.stream(
            model=self.model,
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            system=system or None,
            messages=convo,
            **kwargs,
        ) as stream:
            yield from stream.text_stream
