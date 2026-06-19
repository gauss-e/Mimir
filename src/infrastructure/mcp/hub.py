"""A live MCP client: keep servers connected and dispatch tool calls.

Where ``manager.probe_all`` opens each server, lists its tools, and closes
again (that's what ``/mcp`` reports), the **hub** keeps the sessions *open* for
the life of the app so the agent can actually *call* their tools mid-chat —
this is the "use MCP" half of Mimir, the way Claude Code uses MCP servers.

The MCP SDK is async and its stdio sessions are bound to one event loop, so the
hub runs a private asyncio loop in a background thread. A single long-lived task
(:meth:`_serve`) opens every server inside one ``AsyncExitStack`` and parks on a
stop event; tearing the stack down in the *same* task it was built in sidesteps
anyio's "cancel scope in a different task" trap. Sync callers (the Textual
worker thread) reach the loop via ``run_coroutine_threadsafe``.

Tools are namespaced ``<server>__<tool>`` so two servers can expose a tool of
the same name without clashing.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from config import _resolve_env
from infrastructure.llm import ToolSpec

from .registry import McpServerConfig

CONNECT_TIMEOUT = 30.0   # seconds to bring a local server up
LOGIN_TIMEOUT = 300.0    # seconds allowed for an interactive OAuth browser login
CALL_TIMEOUT = 60.0      # seconds for one tool call


@dataclass(frozen=True)
class _Tool:
    qualified: str        # name exposed to the model: "<server>__<tool>"
    server: str
    tool: str             # the server's own tool name
    spec: ToolSpec


@dataclass(frozen=True)
class HubStatus:
    connected: list[str]
    failed: dict[str, str]      # server name -> error
    tool_count: int


class McpHub:
    """Persistent connections to MCP servers + a sync tool-dispatch bridge."""

    def __init__(self, servers: list[McpServerConfig]):
        self._servers = [s for s in servers if s.is_runnable()]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None   # created on the loop
        self._ready_box: list[HubStatus] = []
        self._ready_signal = threading.Event()
        self._sessions: dict[str, object] = {}     # server -> ClientSession
        self._tools: dict[str, _Tool] = {}         # qualified name -> _Tool
        self.status = HubStatus(connected=[], failed={}, tool_count=0)

    # --- lifecycle (call start/stop from a worker thread) ----------------
    def start(self) -> HubStatus:
        """Launch the loop thread and connect every server. Blocking."""
        if not self._servers:
            return self.status
        try:  # MCP SDK is the optional `notion` extra; degrade if absent.
            import mcp  # noqa: F401
        except ImportError:
            self.status = HubStatus(
                connected=[],
                failed={s.name: "MCP SDK missing (uv sync --extra notion)"
                        for s in self._servers},
                tool_count=0,
            )
            return self.status

        # _serve fills _ready_box and sets _ready_signal once every server is up
        # (or failed), then parks until stop(); wait for that signal here. Set
        # both up *before* scheduling the task to avoid a startup race.
        self._ready_box: list[HubStatus] = []
        self._ready_signal = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        # A remote server may need a browser login; wait long enough for it.
        wait = LOGIN_TIMEOUT if any(s.is_remote() for s in self._servers) else CONNECT_TIMEOUT
        self._ready_signal.wait(timeout=wait + 10)
        if self._ready_box:
            self.status = self._ready_box[0]
        return self.status

    def stop(self) -> None:
        if self._loop is None:
            return
        if self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)

    # --- the long-lived connection task ----------------------------------
    async def _open_streams(self, stack, cfg: McpServerConfig):
        """Open the right transport for ``cfg`` and return (read, write)."""
        if cfg.is_remote():
            from mcp.client.streamable_http import streamablehttp_client

            from .oauth import build_oauth_provider

            # OAuth (SSO) kicks in lazily: the SDK opens the browser + catches
            # the loopback callback on the first request that needs auth.
            auth = build_oauth_provider(cfg.url, cfg.name)
            streams = await stack.enter_async_context(
                streamablehttp_client(cfg.url, auth=auth)
            )
            return streams[0], streams[1]  # (read, write, get_session_id)

        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=_resolve_env(dict(cfg.env)) or None,
        )
        return await stack.enter_async_context(stdio_client(params))

    async def _serve(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession

        self._stop_event = asyncio.Event()
        connected: list[str] = []
        failed: dict[str, str] = {}

        async with AsyncExitStack() as stack:
            for cfg in self._servers:
                try:
                    read, write = await self._open_streams(stack, cfg)
                    session = await stack.enter_async_context(ClientSession(read, write))
                    # Remote servers may need an interactive browser login, so
                    # give them far longer to initialize than a local process.
                    init_timeout = LOGIN_TIMEOUT if cfg.is_remote() else CONNECT_TIMEOUT
                    await asyncio.wait_for(session.initialize(), timeout=init_timeout)
                    tools = (await session.list_tools()).tools
                    self._sessions[cfg.name] = session
                    for t in tools:
                        qualified = self._qualify(cfg.name, t.name)
                        self._tools[qualified] = _Tool(
                            qualified=qualified,
                            server=cfg.name,
                            tool=t.name,
                            spec=ToolSpec(
                                name=qualified,
                                description=t.description or "",
                                input_schema=t.inputSchema or {"type": "object"},
                            ),
                        )
                    connected.append(cfg.name)
                except Exception as e:  # one bad server must not sink the rest
                    failed[cfg.name] = str(e).splitlines()[0][:200]

            self._ready_box.append(
                HubStatus(connected=connected, failed=failed, tool_count=len(self._tools))
            )
            self._ready_signal.set()
            await self._stop_event.wait()
        # stack unwinds here, in this same task — safe teardown.

    def _qualify(self, server: str, tool: str) -> str:
        name = f"{server}__{tool}"
        if len(name) <= 64:
            return name
        # Anthropic caps tool names at 64 chars; keep the server prefix, trim tool.
        return name[:64]

    # --- what the agent loop consumes ------------------------------------
    def tools(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def has_tools(self) -> bool:
        return bool(self._tools)

    def call(self, qualified: str, args: dict) -> str:
        """Run a tool by its qualified name; return flattened text. Sync."""
        tool = self._tools.get(qualified)
        if tool is None:
            return f"ERROR: unknown tool {qualified!r}"
        if self._loop is None:
            return "ERROR: MCP hub not running"
        fut = asyncio.run_coroutine_threadsafe(self._call(tool, args), self._loop)
        try:
            return fut.result(timeout=CALL_TIMEOUT)
        except Exception as e:
            return f"ERROR calling {qualified}: {str(e).splitlines()[0][:300]}"

    async def _call(self, tool: _Tool, args: dict) -> str:
        session = self._sessions[tool.server]
        result = await asyncio.wait_for(
            session.call_tool(tool.tool, args or {}), timeout=CALL_TIMEOUT
        )
        text = _text_of(result)
        if getattr(result, "isError", False):
            return f"ERROR: {text}" if text else "ERROR (tool reported failure)"
        return text or "(tool returned no text content)"


def _text_of(result) -> str:
    """Flatten an MCP tool result into plain text."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)
