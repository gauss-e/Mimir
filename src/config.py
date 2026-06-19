"""Configuration loading for Mimir.

Config is a TOML file (``config.toml`` in the repo root). String values of the form
``env:VAR_NAME`` are resolved from the environment at load time, so secrets stay
out of the file. Resolution order for the config path:

1. explicit ``path`` argument
2. ``$MIMIR_CONFIG``
3. ``./config.toml`` in the current working directory

If no file is found, sensible defaults are used and provider keys fall back to
the conventional env vars (``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``).
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# profile.md lives inside the profiles module so the module is self-contained.
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "profiles" / "profile.md"

# Built-in MCP servers ship inside the package (src/resources/mcp.local.json)
# and load on every startup — no user setup required.
BUILTIN_MCP_JSON = Path(__file__).resolve().parent / "resources" / "mcp.local.json"
# Per-user MCP home. Created on startup if missing; an optional mcp.json inside
# holds extra/override servers layered on top of the built-ins.
MIMIR_HOME = Path.home() / ".mimir"
USER_MCP_JSON = MIMIR_HOME / "mcp.json"


def _resolve_env(value):
    """Recursively resolve ``env:VAR`` strings against the environment."""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


@dataclass
class Config:
    provider: str = "anthropic"
    # provider name -> its settings (model, api_key, host, ...)
    llm: dict[str, dict] = field(default_factory=dict)
    profile_path: Path = DEFAULT_PROFILE_PATH
    notion: dict = field(default_factory=dict)
    # Merged { "mcpServers": {...} } from the built-in resources/mcp.local.json
    # + the user's ~/.mimir/mcp.json. The sole source of MCP servers — nothing
    # is hardcoded.
    mcp_json: dict = field(default_factory=dict)

    def llm_settings(self) -> dict:
        """Settings for the currently selected provider, with env fallbacks."""
        settings = dict(self.llm.get(self.provider, {}))
        if self.provider == "openai" and not settings.get("api_key"):
            settings["api_key"] = os.environ.get("OPENAI_API_KEY", "")
        if self.provider == "anthropic" and not settings.get("api_key"):
            settings["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
        return settings


def _find_config_path(path: str | None) -> Path | None:
    for candidate in (path, os.environ.get("MIMIR_CONFIG"), "config.toml"):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _read_mcp_servers(path: Path) -> dict:
    """Read one Claude-Code-style file's ``mcpServers`` object (or ``{}``)."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = _resolve_env(json.load(fh))
    return (data or {}).get("mcpServers") or {}


def _load_mcp_json() -> dict:
    """Merge the built-in MCP servers with the user's optional extra config.

    Two layers, read entirely from JSON — nothing is hardcoded in code:

    1. ``src/resources/mcp.local.json`` — built-in servers bundled with Mimir;
       loaded on every startup, no setup needed.
    2. ``~/.mimir/mcp.json`` — optional per-user extra servers / overrides.
       ``~/.mimir`` is created on startup if absent; the file is read only when
       present. ``$MIMIR_MCP_JSON`` overrides this path.

    The user file overlays the built-ins, so a server defined there wins on a
    name clash. ``env:NAME`` strings resolve from the environment, same as TOML.
    """
    MIMIR_HOME.mkdir(parents=True, exist_ok=True)
    user_path = Path(os.environ.get("MIMIR_MCP_JSON") or USER_MCP_JSON)
    servers: dict = {}
    servers.update(_read_mcp_servers(BUILTIN_MCP_JSON))
    servers.update(_read_mcp_servers(user_path))
    return {"mcpServers": servers}


def load_config(path: str | None = None) -> Config:
    found = _find_config_path(path)
    raw: dict = {}
    if found:
        with found.open("rb") as fh:
            raw = _resolve_env(tomllib.load(fh))

    llm_raw = raw.get("llm", {})
    provider = llm_raw.get("provider", "anthropic")
    # everything under [llm.*] except the scalar "provider" key is per-provider
    providers = {k: v for k, v in llm_raw.items() if isinstance(v, dict)}

    profile_raw = raw.get("profile", {}).get("path")
    profile_path = Path(profile_raw) if profile_raw else DEFAULT_PROFILE_PATH

    return Config(
        provider=provider,
        llm=providers,
        profile_path=profile_path,
        notion=raw.get("notion", {}),
        mcp_json=_load_mcp_json(),
    )
