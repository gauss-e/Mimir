# Mimir

A personal career-advisor agent. Mimir keeps a *living profile* of you and
advises from your real stack and goals — never generic anxiety.

A full-screen **TUI** (built with [Textual](https://textual.textualize.io/)):
launch it and you drop into a Claude-Code-style interface — scrolling
conversation pane with a docked input box and a status footer. Dependencies are
managed with [uv](https://docs.astral.sh/uv/).

This is the first slice of the architecture (see `CLAUDE.md` in the parent
project for the full vision). It ships two modules:

- **`llm/`** — a pluggable LLM client. One interface, three backends: OpenAI,
  Anthropic, and local Ollama. Selected and configured via `config.toml`.
  (Ollama uses only the stdlib; OpenAI/Anthropic SDKs are optional extras.)
- **`profile/`** — the user-profile (用户画像) module. It ingests your career
  info from a source, distils it into a structured `profile.md`, and on every
  startup decides whether onboarding is still needed.

## Install

```bash
cd Mimir
uv sync --extra anthropic        # or: --extra openai / --extra mcp / --extra files
```

`uv sync` creates the virtualenv and installs deps. Pick the extras you need;
Ollama needs none.

## Configure

Create `config.toml` in the repo root (gitignored). Minimal Anthropic example:

```toml
[llm]
provider = "anthropic"

[llm.anthropic]
api_key = "env:ANTHROPIC_API_KEY"   # resolved from the env at load
```

```bash
export ANTHROPIC_API_KEY=sk-...   # or OPENAI_API_KEY; Ollama needs no key
```

Set `[llm].provider` (`anthropic` / `openai` / `ollama`) to choose your backend.

## Run

```bash
uv run mimir                     # dev: run from this project dir
```

Install it as a real command on your PATH:

```bash
uv tool install .                # then just: mimir
# update after code changes:  uv tool install . --reinstall
```

Config is read from `./config.toml` (or `$MIMIR_CONFIG`, or `-c <path>`), so run
`mimir` from the project dir, or point it at the file:

```bash
mimir -c /path/to/config.toml
```

### Terminal rendering

Mimir is a full-screen TUI; it looks best in a true-color terminal. If borders,
colors, or layout look off:

- Use a modern terminal: iTerm2 / Kitty / WezTerm / Ghostty / the VS Code
  integrated terminal (avoid the stock macOS Terminal.app — limited color).
- Ensure 256/true color: `export TERM=xterm-256color` (and `COLORTERM=truecolor`).
- Use a Nerd Font (or any modern monospace) so box-drawing glyphs align.
- Give the window some size — a tiny window clips the input box and footer.
- Commands start with `/`. A CJK input method may emit the fullwidth `／`;
  Mimir normalizes a leading `／` to `/`, but switching the IME to English /
  half-width is the surest way to type commands.

## Profile flow

1. **Startup check** — if `profile.md` already holds real content, Mimir skips
   onboarding and goes straight to chat.
2. **Onboarding** (only when empty) — provide career info one of these ways:
   - type a one-line summary and press Enter
   - `/file <path>` — load a résumé (`.txt` / `.md` / `.pdf` / `.docx`)
   - `/notion <query>` — pull from Notion via an MCP server (configure `[notion]`)
3. The LLM distils the raw material into a structured `profile.md`, which then
   grounds every future conversation.

In-app commands: `/profile` (show), `/reload`, `/file`, `/notion`, `/mcp`,
`/help`, `/quit`. Keys: `ctrl+l` clear, `ctrl+q` / `ctrl+c` quit.

## MCP servers

Mimir reaches external tools (Notion, the filesystem, Office docs, …) through
**MCP servers**, configured Claude-Code-style in two JSON files (nothing is
hardcoded — a server exists only if it's listed here):

- `src/resources/mcp.local.json` — **built-in**, committed, shipped in the
  wheel. Loaded on every startup, no setup. Best place for default servers.
- `~/.mimir/mcp.json` — **optional per-user** extra servers / overrides.
  `~/.mimir` is created on first launch; drop an `mcp.json` in it to add or
  override servers. Merged on top of the built-ins, so a same-named server here
  wins. (Best for remote servers reached over OAuth SSO — no tokens to commit.)

```json
{
  "mcpServers": {
    "notion":     { "type": "http", "url": "https://mcp.notion.com/mcp" },
    "filesystem": { "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
  }
}
```

Run `/mcp` in the TUI to see how many servers connected and how many tools each
exposes; `/mcp add <name> <cmd|url> <args…>` adds one at runtime. Needs the MCP
SDK: `uv sync --extra mcp`.

## Layout

```
Mimir/
  pyproject.toml      # packaging (hatchling) + uv config
  config.toml         # local config (gitignored)
  src/                # flat layout: modules ship top-level (src/ stripped on build)
    cli.py            # entry point (console script `mimir`)
    app.py            # Textual TUI (scrolling chat + input + footer)
    config.py         # TOML + env config + built-in/user mcp.json merge
    resources/        # bundled package data
      mcp.local.json  # built-in MCP servers (committed, always loaded)
    infrastructure/   # cross-cutting plumbing (not domain logic)
      llm/            # provider-agnostic LLM client
        base.py client.py providers/{openai,anthropic,ollama}_provider.py
      mcp/            # MCP server registry (JSON-driven) + hub + probe (/mcp)
        registry.py hub.py oauth.py manager.py
    profiles/         # user-profile module (plural: avoids stdlib `profile` clash)
      service.py store.py parser.py
      profile.md      # generated (gitignored)
      sources/        # file / text / notion(MCP)
```
