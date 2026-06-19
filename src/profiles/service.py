"""Profile orchestration: onboarding check + ingestion pipeline.

All ``ingest_*`` calls are blocking (LLM + I/O).
"""

from __future__ import annotations

from infrastructure.llm import LLMProvider
from . import parser
from .sources import FileSource, NotionSource, Source, TextSource
from .store import ProfileStore


class ProfileService:
    def __init__(self, store: ProfileStore, llm: LLMProvider, notion_config: dict | None = None):
        self.store = store
        self.llm = llm
        self.notion_config = notion_config or {}

    # --- startup ---------------------------------------------------------
    def needs_onboarding(self) -> bool:
        """True when there's no usable profile yet, so we should ask the user
        for career info."""
        return not self.store.has_content()

    def summary(self) -> str:
        return self.store.read()

    # --- ingestion -------------------------------------------------------
    def ingest(self, source: Source) -> str:
        """Fetch raw text from ``source``, distil to a profile, persist it."""
        raw = source.fetch()
        profile_md = parser.parse_to_profile(self.llm, raw)
        self.store.write(profile_md)
        return profile_md

    def ingest_text(self, text: str) -> str:
        return self.ingest(TextSource(text))

    def ingest_file(self, path: str) -> str:
        return self.ingest(FileSource(path))

    def ingest_notion(self, query: str) -> str:
        return self.ingest(NotionSource(self.notion_config, query))
