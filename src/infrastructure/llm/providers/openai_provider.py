"""OpenAI (and OpenAI-compatible) backend."""

from __future__ import annotations

from typing import Iterator

from ..base import LLMProvider, Message


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str | None = None,
        **_,
    ):
        from openai import OpenAI

        if not api_key:
            raise ValueError("OpenAI provider needs an api_key (or OPENAI_API_KEY).")
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)

    def _payload(self, messages: list[Message]) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def chat(self, messages: list[Message], **kwargs) -> str:
        resp = self._client.chat.completions.create(
            model=self.model, messages=self._payload(messages), **kwargs
        )
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self.model, messages=self._payload(messages), stream=True, **kwargs
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
