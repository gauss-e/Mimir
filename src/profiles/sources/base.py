"""Source interface: anything that can yield raw career text."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Source(ABC):
    @abstractmethod
    def fetch(self) -> str:
        """Return raw career text. Blocking is fine — callers run this off the
        UI thread."""
