"""MCP integration: a registry of JSON-configured MCP servers, a live hub that
keeps them connected, and a manager that probes them (surfaced via ``/mcp``).

Singular ``mcp`` is safe here because it's nested (``infrastructure.mcp``): a
*top-level* ``mcp`` would shadow the installed MCP SDK package, but a nested one
won't.
"""

from .hub import HubStatus, McpHub
from .manager import ProbeResult, probe_all
from .registry import McpServerConfig, load_servers

__all__ = [
    "McpServerConfig",
    "load_servers",
    "ProbeResult",
    "probe_all",
    "McpHub",
    "HubStatus",
]
