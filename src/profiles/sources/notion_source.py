"""Career info pulled from Notion through an MCP server.

Recruiting/Notion platforms expose no convenient API for this, so we drive a
Notion **MCP server** over stdio (the same mechanism editors use). The server
command and tool names are config-driven so this works with whichever Notion MCP
build the user has, e.g.::

    [notion]
    command = "npx"
    args = ["-y", "@notionhq/notion-mcp-server"]
    env = { NOTION_TOKEN = "env:NOTION_TOKEN" }
    search_tool = "search"
    fetch_tool = "fetch"

This is best-effort: it searches for ``query``, fetches the top hits, and
concatenates their text for the LLM to distil.
"""

from __future__ import annotations

import asyncio

from .base import Source

_MAX_RESULTS = 5


class NotionSource(Source):
    def __init__(self, notion_config: dict, query: str):
        self.cfg = notion_config or {}
        self.query = query

    def fetch(self) -> str:
        if not self.cfg.get("command"):
            raise RuntimeError(
                "Notion source needs a [notion] command in config.toml "
                "(the MCP server to launch). See config.example.toml."
            )
        return asyncio.run(self._fetch_async())

    async def _fetch_async(self) -> str:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:
            raise RuntimeError(
                "Notion source needs the MCP SDK. Install with: pip install mcp"
            ) from e

        params = StdioServerParameters(
            command=self.cfg["command"],
            args=self.cfg.get("args", []),
            env=self.cfg.get("env") or None,
        )
        search_tool = self.cfg.get("search_tool", "search")
        fetch_tool = self.cfg.get("fetch_tool", "fetch")

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                available = {t.name for t in (await session.list_tools()).tools}
                if search_tool not in available:
                    raise RuntimeError(
                        f"Notion MCP server has no '{search_tool}' tool. "
                        f"Available: {', '.join(sorted(available))}. "
                        "Set [notion].search_tool / fetch_tool accordingly."
                    )

                search_res = await session.call_tool(
                    search_tool, {"query": self.query}
                )
                hits = _text_of(search_res)

                # Best-effort: also try to fetch detailed pages if the tool exists.
                detail = ""
                if fetch_tool in available:
                    for page_id in _guess_ids(hits)[:_MAX_RESULTS]:
                        try:
                            res = await session.call_tool(fetch_tool, {"id": page_id})
                            detail += "\n\n" + _text_of(res)
                        except Exception:
                            continue
                return (hits + detail).strip()


def _text_of(result) -> str:
    """Flatten an MCP tool result into plain text."""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _guess_ids(text: str) -> list[str]:
    """Pull anything that looks like a Notion page/block id from search output."""
    import re

    ids = re.findall(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
                     r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", text)
    seen: list[str] = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return seen
