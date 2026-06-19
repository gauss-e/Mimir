"""Turn raw career text into a structured profile.md via the LLM."""

from __future__ import annotations

from infrastructure.llm import LLMProvider, Message

_SYSTEM = """\
You are Mimir's profile extractor. Given raw career material (a résumé, notes,
or exported documents), produce a clean, structured Markdown user profile in the
SAME language as the source material.

Rules:
- Output ONLY the Markdown body. No code fences, no preamble.
- Use these top-level sections (omit a section only if there is truly nothing):
  ## 基本信息 / Basics
  ## 技能 / Skills
  ## 工作与项目经历 / Experience & Projects
  ## 成就与里程碑 / Achievements
  ## 职业目标 / Career Goals
  ## 性格与价值观 / Personality & Values
- Be faithful: never invent facts. If something is unknown, leave it out.
- Prefer concise bullet points over prose.
- Keep concrete signals (companies, dates, tech stack, metrics) intact.
"""


def parse_to_profile(llm: LLMProvider, raw_text: str) -> str:
    """Return a Markdown profile distilled from ``raw_text``."""
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("No career text provided to parse.")
    messages = [
        Message("system", _SYSTEM),
        Message("user", f"Raw career material:\n\n{raw_text}"),
    ]
    return llm.chat(messages).strip()
