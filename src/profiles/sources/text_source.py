"""Raw pasted text."""

from __future__ import annotations

from .base import Source


class TextSource(Source):
    def __init__(self, text: str):
        self.text = text

    def fetch(self) -> str:
        return self.text
