"""Connect to MCP servers and report what loaded.

Probing launches each server over stdio, initializes a session, and lists its
tools — that's what ``/mcp`` reports. Probes are best-effort and time-boxed so a
broken server can't hang the UI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from config import _resolve_env

from .registry import McpServerConfig

PROBE_TIMEOUT = 20.0  # seconds per server


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    tool_count: int = 0
    tools: tuple[str, ...] = ()
    error: str = ""


async def _probe_one(cfg: McpServerConfig) -> ProbeResult:
    if not cfg.is_runnable():
        return ProbeResult(cfg.name, ok=False, error="no command configured")
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return ProbeResult(cfg.name, ok=False, error="MCP SDK missing (uv sync --extra notion)")

    params = StdioServerParameters(
        command=cfg.command,
        args=list(cfg.args),
        env=_resolve_env(dict(cfg.env)) or None,
    )
    try:
        async def run() -> ProbeResult:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = [t.name for t in (await session.list_tools()).tools]
                    return ProbeResult(cfg.name, ok=True, tool_count=len(tools),
                                       tools=tuple(tools))

        return await asyncio.wait_for(run(), timeout=PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        return ProbeResult(cfg.name, ok=False, error=f"timeout after {PROBE_TIMEOUT:.0f}s")
    except Exception as e:  # launch failure, missing binary, bad creds, …
        return ProbeResult(cfg.name, ok=False, error=str(e).splitlines()[0][:200])


async def _probe_all(configs: list[McpServerConfig]) -> list[ProbeResult]:
    return list(await asyncio.gather(*(_probe_one(c) for c in configs)))


def probe_all(configs: list[McpServerConfig]) -> list[ProbeResult]:
    """Blocking probe of every server. Call from a worker thread, not the UI."""
    if not configs:
        return []
    return asyncio.run(_probe_all(configs))
