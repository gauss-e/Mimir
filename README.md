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
uv sync --extra anthropic        # or: --extra openai / --extra notion / --extra files
```

`uv sync` creates the virtualenv and installs deps. Pick the extras you need;
Ollama needs none.

## Configure

```bash
cp config.example.toml config.toml
export ANTHROPIC_API_KEY=sk-...   # or OPENAI_API_KEY; Ollama needs no key
```

Edit `[llm].provider` to choose your backend.

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

## Profile flow

1. **Startup check** — if `profile.md` already holds real content, Mimir skips
   onboarding and goes straight to chat.
2. **Onboarding** (only when empty) — provide career info one of these ways:
   - type a one-line summary and press Enter
   - `/file <path>` — load a résumé (`.txt` / `.md` / `.pdf` / `.docx`)
   - `/notion <query>` — pull from Notion via an MCP server (configure `[notion]`)
3. The LLM distils the raw material into a structured `profile.md`, which then
   grounds every future conversation.

In-app commands: `/profile` (show), `/reload`, `/file`, `/notion`, `/help`,
`/quit`. Keys: `ctrl+l` clear, `ctrl+c` quit.

## Layout

```
Mimir/
  pyproject.toml      # packaging (hatchling) + uv config
  config.toml         # local config (gitignored)
  src/mimir/
    __main__.py       # entry point (mimir / python -m mimir)
    app.py            # Textual TUI (scrolling chat + docked input + footer)
    config.py         # TOML + env config
    llm/              # provider-agnostic LLM client
      base.py client.py providers/{openai,anthropic,ollama}_provider.py
    profile/          # user-profile module
      service.py store.py parser.py
      profile.md      # generated (gitignored)
      sources/        # file / text / notion(MCP)
```
