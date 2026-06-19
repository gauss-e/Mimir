# Mimir — CLAUDE.md

Project-specific instructions for the **Mimir** subproject. For the overall
Jarvis vision/architecture, see `../CLAUDE.md` (parent). This file governs code
under `Mimir/` only.

## What Mimir is

A personal career-advisor **TUI** (Textual). Launch → drop into a
Claude-Code-style full-screen interface: scrolling conversation pane + docked
bottom input + status footer + streaming replies. First slice of the larger
Jarvis architecture.

Two modules ship today:

- **`llm/`** — provider-agnostic LLM client. One interface (`LLMProvider`),
  three backends: `openai`, `anthropic`, `ollama`. Selected via `config.toml`.
  Imports are deferred in `llm/client.py` so Ollama users (stdlib only) never
  import the OpenAI/Anthropic SDKs.
- **`profile/`** — the user-profile (用户画像) module. Ingests career info from a
  source (text / file / Notion-MCP), distils it to a structured `profile.md`,
  and on startup decides whether onboarding is still needed.

## Layout (flat src layout — modules straight under src/)

```
Mimir/
  pyproject.toml          # hatchling packaging + uv; console script `mimir`
  config.toml             # local config + secrets (gitignored; no committed template)
  src/                    # package root (hatchling strips the src/ prefix on build)
    cli.py                # entry point -> console script `mimir = "cli:main"`
    app.py                # Textual TUI
    config.py             # TOML + env (env:VAR) config + built-in/user mcp.json merge
    resources/            # bundled package data
      mcp.local.json      # built-in MCP servers (committed, ship in wheel, always loaded)
    infrastructure/       # cross-cutting plumbing (not domain logic)
      llm/                # base.py client.py providers/{openai,anthropic,ollama}_provider.py
      mcp/                # MCP registry (JSON-driven) + hub + oauth + manager (probe)
    profiles/             # service.py store.py parser.py + profile.md (generated, gitignored)
      sources/            # file_source.py text_source.py notion_source.py
```

There is **no inner `mimir/` package dir** — modules live directly under `src/`
and ship as top-level (`cli`, `app`, `config`, `infrastructure`, `profiles`).
The technical plumbing (`llm`, `mcp`) lives under one `infrastructure/`
package; domain modules import `infrastructure.llm` / `infrastructure.mcp`.
Because the top-level names ship as commands, **a top-level module must not
share a name with the stdlib or an installed SDK**: the user-profile package is
named **`profiles`** (plural), not `profile`, since a top-level `profile`
shadows/collides with the stdlib `profile` once installed as a command. The MCP
package is fine as singular **`mcp`** *because it's nested* (`infrastructure.mcp`)
— it's no longer top-level, so it can't shadow the installed `mcp` SDK. A
**top-level** `mcp` would; keep this in mind before adding new top-level modules.

Packaging: `[tool.hatch.build.targets.wheel]` uses `only-include = ["src"]` +
`sources = ["src"]` so `src/` is stripped on build and the modules install at
top level. There is exactly one copy of the source under `src/`; the
`mimir-*.dist-info` in `.venv` is editable-install metadata, not a duplicate.

## Conventions

- **Deps**: `uv` only (not poetry / raw pip). `uv sync --extra <name>` for
  optional backends (`anthropic` / `openai` / `mcp` / `files`).
- **Run (dev)**: `uv run mimir`. **Install**: `uv tool install .` →
  `mimir` on PATH; re-`--reinstall` after code changes.
- **Style**: PEP 8, type annotations on signatures, `from __future__ import
  annotations`, dataclasses for config/DTOs. black / isort / ruff.
- **Config**: never commit secrets. Use `env:VAR_NAME` strings in
  `config.toml`; they resolve from the environment at load. Keep `config.toml`
  and `profile.md` gitignored. No committed template — edit `config.toml`
  directly; do not (re)create `config.example.toml`.
- **Surgical changes**: match surrounding style; touch only what the task needs.

## TUI rules

- Launch must land the user directly in the full-screen interface (scrollback
  pane + docked bottom input + streaming), Claude-Code style.
- Quitting must always work: `/quit` (or `/exit` `/q`), and key bindings
  `ctrl+q` / `ctrl+c`. Quit bindings are `priority` so a focused `Input`
  cannot swallow them.
- Conversation must fit any terminal: messages wrap, nothing clips behind the
  docked input or footer. Keep a bottom gutter under the chat for the input.
- LLM/network calls run in `@work(thread=True)` workers; update widgets via
  `call_from_thread`. Never block the event loop.
- Some terminals deliver `/` (and `?`, `\`) as a key with `character=None`, so
  Textual's `Input` drops it. `MimirApp.on_key` recovers these via
  `_KEY_FIXUPS`; a CJK IME's fullwidth `／` is normalized on submit. Keep both.

## MCP

- Mimir is an MCP **client** (like Claude Code): it connects to MCP servers and
  lets the agent *call* their tools mid-chat. Files in `infrastructure/mcp/`:
  - `registry.py` (`load_servers`) builds the server set **entirely from the
    merged JSON** — nothing is hardcoded (no presets, no `[mcp]` TOML table).
    `config.py::_load_mcp_json` merges two Claude-Code-style layers'
    `mcpServers` objects: the **built-in** `src/resources/mcp.local.json`
    (committed, shipped in the wheel, loaded on every startup — no setup) then
    the **user** `~/.mimir/mcp.json` (optional extra servers / overrides), so
    **the user file wins on a name clash**. On startup `~/.mimir` is created if
    absent; its `mcp.json` is read only when present. `$MIMIR_MCP_JSON`
    overrides the user-file path; `env:NAME` values resolve like TOML. A server
    listed in either layer is both defined and active, just like Claude Code.
  - `hub.py` (`McpHub`) is the live client: it keeps every server **connected**
    for the life of the app and dispatches tool calls. The MCP SDK is async and
    its stdio sessions are loop-bound, so the hub owns a private asyncio loop in
    a background thread; a single long-lived task opens all servers in one
    `AsyncExitStack` and parks on a stop event — tearing the stack down in the
    same task it was built avoids anyio's "cancel scope in a different task"
    error. Sync callers reach it via `run_coroutine_threadsafe`. Tools are
    namespaced `<server>__<tool>`.
  - `oauth.py` handles **remote** servers (Notion's hosted `mcp.notion.com/mcp`,
    etc.) over streamable HTTP with OAuth 2.0 SSO — no static token. It reuses
    the SDK's `OAuthClientProvider` (PKCE + dynamic registration + refresh); we
    only supply a browser `redirect_handler` and a one-shot loopback
    `callback_handler` (fixed `http://localhost:8765/callback`). Tokens persist
    in `~/.mimir/oauth/<server>.json` (mode 600) via a `FileTokenStorage`
    (SDK `TokenStorage`). A config entry is remote when it has a `url` (+ optional
    `type`/`transport` = `http`/`sse`); `McpServerConfig.is_remote()` switches
    the hub between `stdio_client` and `streamablehttp_client`. Remote servers get
    `LOGIN_TIMEOUT` (300s) to allow the interactive login.
  - `manager.py` (`probe_all`) is the older one-shot probe (open → list → close);
    still exported but no longer wired into `/mcp`.
- Chat: when the hub has tools and the backend `supports_tools`, `_chat` routes
  through `LLMProvider.run_tools` (the agentic loop: stream text → run any
  `tool_use` via `hub.call` → feed `tool_result` back → repeat). Implemented for
  Anthropic and Ollama (`/api/chat` tools; needs a tool-capable model like
  llama3.1/qwen2.5/mistral — gemma has no tool template). Backends without
  `run_tools` fall back to plain `stream`.
- `/mcp` shows live connection status. `/mcp add <name> <cmd> <args…>` adds a
  stdio server; `/mcp add <name> <https-url>` adds a remote (OAuth) server. Both
  reconnect the hub and print a config snippet to persist. `/mcp logout <name>`
  clears cached OAuth tokens (forces re-login). Hub start/reconnect runs in a
  worker thread (it launches subprocesses / opens the browser).
- The MCP SDK is the optional `mcp` extra; degrade gracefully when absent
  (hub reports the servers as failed; chat still works without tools).

## Verify before done

Exercise the real flows headlessly with Textual's pilot (stub the LLM, no
network): onboarding → ingest → streaming chat → quit. Confirm quit works and
long messages wrap before declaring a task complete.
