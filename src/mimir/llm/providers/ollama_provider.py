"""Local Ollama backend over its HTTP API, using only the stdlib (urllib)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from ..base import LLMProvider, Message


class OllamaProvider(LLMProvider):
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

    def _request(self, messages: list[Message], stream: bool):
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": stream,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=self.timeout)

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
