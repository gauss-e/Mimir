"""User-profile (用户画像) module.

Ingests career information from a source (file, pasted text, or Notion via MCP),
asks the LLM to distil it into a structured ``profile.md``, and exposes whether
onboarding is still needed on startup.
"""

from .service import ProfileService
from .store import ProfileStore

__all__ = ["ProfileService", "ProfileStore"]
