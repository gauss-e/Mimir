"""Read/write access to ``profile.md`` plus the "is it filled in?" check."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# Below this many chars of real prose (frontmatter/headers/blanks stripped),
# the profile is treated as empty and onboarding is triggered.
MIN_CONTENT_CHARS = 40

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _strip_scaffolding(text: str) -> str:
    """Remove frontmatter, markdown headings, and blank lines to estimate
    how much actual profile content exists."""
    body = _FRONTMATTER.sub("", text, count=1)
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # drop empty bullet placeholders like "- " or "- 待补充"
        cleaned = stripped.lstrip("-*> ").strip()
        if cleaned in ("", "_", "待补充", "TBD", "N/A"):
            continue
        lines.append(cleaned)
    return "\n".join(lines)


class ProfileStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.exists() else ""

    def has_content(self) -> bool:
        """True if profile.md exists and holds a meaningful amount of text."""
        if not self.exists():
            return False
        return len(_strip_scaffolding(self.read())) >= MIN_CONTENT_CHARS

    def write(self, content: str) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not _FRONTMATTER.match(content):
            content = self._with_frontmatter(content)
        self.path.write_text(content, encoding="utf-8")
        return self.path

    @staticmethod
    def _with_frontmatter(body: str) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"---\nlast_updated: {now}\n---\n\n{body.lstrip()}"
