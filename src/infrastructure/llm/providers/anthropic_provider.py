"""Anthropic (Claude) backend.

Anthropic keeps the system prompt out of the message list, so system messages
are concatenated and passed separately.
"""

from __future__ import annotations

from typing import Callable, Iterator

from ..base import Dispatch, LLMProvider, Message, ToolSpec


class AnthropicProvider(LLMProvider):
    supports_tools = True

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

    def run_tools(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        dispatch: Dispatch,
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict], None] | None = None,
        max_steps: int = 8,
    ) -> str:
        """Agentic loop: stream text, run any tool_use blocks, feed results back.

        Each step streams the assistant's visible text (``on_text``), then if the
        model asked for tools we run them through ``dispatch`` and loop with the
        results appended — exactly how Claude Code drives MCP tools.
        """
        system, convo = self._split(messages)
        tool_schema = [
            {"name": t.name, "description": t.description,
             "input_schema": t.input_schema or {"type": "object"}}
            for t in tools
        ]
        final = ""
        for _ in range(max_steps):
            with self._client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system or None,
                messages=convo,
                tools=tool_schema,
            ) as stream:
                for piece in stream.text_stream:
                    if on_text:
                        on_text(piece)
                msg = stream.get_final_message()

            text = "".join(b.text for b in msg.content if b.type == "text")
            if text:
                final = text
            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            if not tool_uses:
                break

            convo.append({"role": "assistant", "content": msg.content})
            results = []
            for tu in tool_uses:
                if on_tool:
                    on_tool(tu.name, tu.input or {})
                output = dispatch(tu.name, tu.input or {})
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": output,
                })
            convo.append({"role": "user", "content": results})
        return final
