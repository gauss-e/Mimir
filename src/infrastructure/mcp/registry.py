"""MCP server registry: servers come entirely from the JSON config.

Mimir talks to external tools (Notion, the filesystem, Office docs, …) through
**MCP servers**. The server set is *not* hardcoded — it is read from two
Claude-Code-style JSON layers (see ``config._load_mcp_json``):

- ``src/resources/mcp.local.json`` — built-in, committed, always loaded.
- ``~/.mimir/mcp.json``            — optional per-user extra servers / overrides.

Both are merged into one ``mcpServers`` object (the user file wins on a name
clash); listing a server there both defines *and* activates it, like Claude Code::

    {
      "mcpServers": {
        "notion":     { "type": "http", "url": "https://mcp.notion.com/mcp" },
        "filesystem": { "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
      }
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # Remote transports: "http" (streamable HTTP) or "sse". Default "stdio".
    transport: str = "stdio"
    url: str = ""

    def is_remote(self) -> bool:
        return self.transport in ("http", "sse")

    def is_runnable(self) -> bool:
        return bool(self.url) if self.is_remote() else bool(self.command)


def _build(name: str, entry: dict) -> McpServerConfig:
    """Build a server config from one JSON ``mcpServers`` entry.

    Understands Claude Code's keys: ``command``/``args``/``env`` for stdio and
    ``type``/``transport`` + ``url`` for remote servers. A bare ``url`` (no
    explicit type) is treated as streamable ``http``.
    """
    url = entry.get("url", "")
    transport = entry.get("type") or entry.get("transport") or "stdio"
    if url and transport == "stdio":
        transport = "http"
    return McpServerConfig(
        name=name,
        command=entry.get("command", ""),
        args=list(entry.get("args", [])),
        env=dict(entry.get("env") or {}),
        transport=transport,
        url=url,
    )


def load_servers(mcp_json: dict | None = None) -> list[McpServerConfig]:
    """Resolve the MCP servers entirely from the merged JSON config.

    ``mcp_json`` is the merged ``{"mcpServers": {...}}`` object produced by
    ``config._load_mcp_json`` (built-in ``mcp.local.json`` overlaid by the
    user's ``~/.mimir/mcp.json``).
    Nothing is hardcoded: a server exists only if it is listed there.
    """
    servers: dict[str, McpServerConfig] = {}
    for name, entry in ((mcp_json or {}).get("mcpServers") or {}).items():
        servers[name] = _build(name, dict(entry or {}))
    return list(servers.values())
