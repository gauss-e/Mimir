"""Local Ollama backend over its HTTP API, using only the stdlib (urllib)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Iterator

from ..base import Dispatch, LLMProvider, Message, ToolSpec


class OllamaProvider(LLMProvider):
    # Ollama's /api/chat carries tools + tool_calls; whether the model actually
    # emits tool_calls depends on its template (llama3.1, qwen2.5, mistral, … do;
    # gemma does not). Unsupported models just answer in text — graceful degrade.
    supports_tools = True

    def __init__(
        self,
        model: str = "llama3",
        host: str = "http://localhost:11434",
        timeout: float = 120.0,
        **_,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _post(self, payload: dict):
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _request(self, messages: list[Message], stream: bool):
        return self._post(
            {
                "model": self.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": stream,
            }
        )

    def chat(self, messages: list[Message], **kwargs) -> str:
        with self._request(messages, stream=False) as resp:
            data = json.loads(resp.read())
        return data["message"]["content"]

    def stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        with self._request(messages, stream=True) as resp:
            for raw in resp:  # one JSON object per line
                line = raw.decode().strip()
                if not line:
                    continue
                piece = json.loads(line).get("message", {}).get("content")
                if piece:
                    yield piece

    def run_tools(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        dispatch: Dispatch,
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict], None] | None = None,
        max_steps: int = 8,
    ) -> str:
        """Agentic loop over Ollama's tool API: stream text, run tool_calls, repeat."""
        convo = [{"role": m.role, "content": m.content} for m in messages]
        tool_schema = [
            {"type": "function",
             "function": {"name": t.name, "description": t.description,
                          "parameters": t.input_schema or {"type": "object"}}}
            for t in tools
        ]
        final = ""
        for _ in range(max_steps):
            payload = {
                "model": self.model,
                "messages": convo,
                "tools": tool_schema,
                "stream": True,
            }
            parts: list[str] = []
            tool_calls: list[dict] = []
            with self._post(payload) as resp:
                for raw in resp:  # one JSON object per line
                    line = raw.decode().strip()
                    if not line:
                        continue
                    msg = json.loads(line).get("message", {})
                    piece = msg.get("content")
                    if piece:
                        parts.append(piece)
                        if on_text:
                            on_text(piece)
                    if msg.get("tool_calls"):
                        tool_calls.extend(msg["tool_calls"])

            text = "".join(parts)
            if text:
                final = text
            if not tool_calls:
                break

            convo.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):  # some builds send a JSON string
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if on_tool:
                    on_tool(name, args)
                output = dispatch(name, args)
                convo.append({"role": "tool", "content": output, "tool_name": name})
        return final
