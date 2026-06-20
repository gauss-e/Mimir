"""ContextBuilder — assembles Mimir's per-turn prompt, Claude-Code style.

Every turn we rebuild the system prompt from *live* state: Mimir's persona,
today's date, what we currently know about the user (the profile, or the fact
that there is none), and the capabilities (MCP tools) available right now.
Rebuilding per-turn means a freshly-ingested profile and newly-connected MCP
servers are picked up automatically — there is no stale, one-shot prompt baked
at startup. The builder itself exists from the moment the app mounts, so the
context is "loaded" before the user says a word.
"""

from __future__ import annotations

from datetime import date

from infrastructure.llm import Message

PERSONA = """\
You are Mimir — in Norse myth the severed head at the well of wisdom, here reborn
as a personal career advisor with the loyalty and reach of Iron Man's JARVIS. You
are proactive, candid, and warm, with the occasional dry wit. Match the user's
language.

Principles:
- Reason only from the user's real stack, history, and goals. Never manufacture
  anxiety or invent facts about them.
- Offer options with trade-offs, not a single verdict — the decision is theirs.
- Be concise. Lead with the point.
- You are a companion for the user's whole career, not a one-off consultant: you
  remember, you reach out, you iterate."""

NO_PROFILE = """\
You do not know this user yet — there is no profile on file. Do not pretend
otherwise, and do not present a form or an onboarding checklist. Instead, in your
own voice, warmly invite them to tell you about themselves so you can begin. They
can simply talk to you, hand you a résumé with /file <path>, or pull from Notion
with /notion <query>."""


class ContextBuilder:
    """Builds the system prompt + message list for each chat turn."""

    def __init__(self, service, hub=None):
        self.service = service
        self.hub = hub

    def system_prompt(self) -> str:
        parts = [PERSONA, f"Today's date: {date.today().isoformat()}."]
        if self.service and self.service.needs_onboarding():
            parts.append(NO_PROFILE)
        else:
            profile = self.service.summary().strip() if self.service else ""
            parts.append(
                "--- WHAT YOU KNOW ABOUT THE USER ---\n"
                f"{profile}\n"
                "--- END ---"
            )
        caps = self._capabilities()
        if caps:
            parts.append(caps)
        return "\n\n".join(parts)

    def messages(self, history: list[Message]) -> list[Message]:
        return [Message("system", self.system_prompt()), *history]

    def _capabilities(self) -> str:
        if not (self.hub and self.hub.has_tools()):
            return ""
        names = sorted(t.name for t in self.hub.tools())
        listed = ", ".join(names[:40])
        return (
            "You can call these tools when they help; their results return to "
            f"you mid-reply:\n{listed}"
        )
