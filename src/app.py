"""Mimir TUI — a Claude-Code-style career advisor.

Full-screen terminal app: a scrolling conversation pane with a docked input box
at the bottom and a status footer. On startup it checks for a populated
``profile.md``; if found it goes straight to chat, otherwise it walks the user
through onboarding (paste a summary, ``/file`` a résumé, or ``/notion``).
"""

from __future__ import annotations

import json

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from config import Config, load_config
from infrastructure.llm import Message, build_provider
from infrastructure.mcp import McpHub, McpServerConfig, load_servers
from profiles import ProfileService, ProfileStore

CHAT_SYSTEM = """\
You are Mimir, a sharp, candid personal career advisor. You know the user via the
profile below. Reason from their actual stack and goals; never manufacture
anxiety. Offer options with trade-offs, not a single verdict. Be concise.

--- USER PROFILE ---
{profile}
--- END PROFILE ---
"""

ONBOARDING = """No profile found yet. Tell me about your career so I can build one:
  • type a summary and press Enter
  • /file <path>      load a résumé (.txt/.md/.pdf/.docx)
  • /notion <query>   pull career info from Notion (needs MCP configured)"""

HELP = ("/profile  /reload  /file <path>  /notion <q>  "
        "/mcp [add <name> <cmd|url> …] [logout <name>]  /help  /quit")


class MimirApp(App):
    CSS = """
    Screen { layout: vertical; }
    /* Chat fills all space between header and the input/footer. */
    #chat { height: 1fr; padding: 1 2; }
    #chat > Static { width: 100%; height: auto; margin: 0 0 1 0; }
    .user   { color: $text; }
    .mimir  { color: $success; }
    .info   { color: $text-muted; }
    .error  { color: $error; }
    /* Input sits in normal flow (not docked) above the footer, fixed height,
       so it never fights the Footer for the bottom edge or clip the chat. */
    #prompt { height: 3; margin: 0 1; border: round $accent; }
    """
    # priority=True so a focused Input can't swallow the quit keys.
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear", "Clear"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.store = ProfileStore(config.profile_path)
        self.service: ProfileService | None = None
        self.llm = None
        self.init_error: str | None = None
        self.mode = "chat"  # "chat" | "onboarding"
        self.history: list[Message] = []
        # MCP servers read entirely from JSON (nothing hardcoded): built-in
        # resources/mcp.local.json overlaid by ~/.mimir/mcp.json. The hub keeps
        # them connected so the agent can call their tools mid-chat.
        self.mcp_servers = load_servers(config.mcp_json)
        self.hub: McpHub | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield Input(id="prompt", placeholder="Message Mimir…  (/help, /quit)")
        yield Footer()

    # --- lifecycle -------------------------------------------------------
    def on_mount(self) -> None:
        self.title = "Mimir"
        self.sub_title = "Career Advisor"
        try:
            self.llm = build_provider(self.config.provider, self.config.llm_settings())
            self.service = ProfileService(self.store, self.llm, self.config.notion)
            model = self.config.llm_settings().get("model", "")
            self.sub_title = f"{self.config.provider}·{model}".rstrip("·")
        except Exception as e:
            self.init_error = str(e)
            self._say("error", f"LLM not ready: {e}")
            self._say("info", "Fix config.toml (or env keys) and restart.")
            return

        if self.service.needs_onboarding():
            self.mode = "onboarding"
            self._say("info", ONBOARDING)
        else:
            self.mode = "chat"
            self._say("info", f"Profile loaded from {self.config.profile_path}.")
            self._say("info", "Ask me anything about your career. /help for commands.")
        n = len(self.mcp_servers)
        if n:
            names = ", ".join(s.name for s in self.mcp_servers)
            self._say("info", f"connecting {n} MCP server(s): {names}…")
            self._start_hub_worker(self.mcp_servers)
        self.query_one(Input).focus()

    def on_unmount(self) -> None:
        if self.hub is not None:
            self.hub.stop()

    # --- input handling --------------------------------------------------
    # Some terminals deliver certain keys with no associated character (e.g.
    # "/" arrives as key="slash" character=None). Textual's Input only inserts
    # keys that carry a printable character, so those keys silently vanish. We
    # recover them here: when a focused prompt sees such a key, insert the glyph.
    _KEY_FIXUPS = {"slash": "/", "question_mark": "?", "backslash": "\\"}

    def on_key(self, event) -> None:
        if event.character is not None:
            return
        glyph = self._KEY_FIXUPS.get(event.key)
        if glyph is None:
            return
        inp = self.query_one("#prompt", Input)
        if self.focused is inp:
            inp.insert_text_at_cursor(glyph)
            event.prevent_default()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one(Input).clear()
        if not text:
            return
        # A CJK input method emits the fullwidth slash "／" (U+FF0F) instead of
        # ASCII "/", so commands typed with the IME on would never match.
        # Normalize a leading fullwidth slash to ASCII.
        if text[:1] == "／":
            text = "/" + text[1:]
        self._say("user", text)
        if text.startswith("/"):
            self._command(text)
        elif self.init_error:
            self._say("error", "LLM not configured — cannot proceed.")
        elif self.mode == "onboarding":
            self._ingest_worker("text", text, "pasted text")
        else:
            self._chat(text)

    def _command(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
        elif cmd == "/help":
            self._say("info", ONBOARDING if self.mode == "onboarding" else HELP)
        elif cmd == "/clear":
            self.action_clear()
        elif cmd == "/profile":
            self._say("info", (self.service.summary() if self.service else "") or "(profile is empty)")
        elif cmd == "/reload":
            if self.service:
                self.mode = "onboarding" if self.service.needs_onboarding() else "chat"
                self._say("info", f"reloaded — mode: {self.mode}")
        elif cmd == "/file":
            if not arg:
                self._say("error", "usage: /file <path>")
            else:
                self._ingest_worker("file", arg, f"file {arg}")
        elif cmd == "/notion":
            if not arg:
                self._say("error", "usage: /notion <search query>")
            else:
                self._ingest_worker("notion", arg, "Notion")
        elif cmd == "/mcp":
            self._mcp_command(arg)
        else:
            self._say("error", f"unknown command: {cmd}")

    def action_clear(self) -> None:
        self.query_one("#chat", VerticalScroll).remove_children()

    # --- chat (streaming) ------------------------------------------------
    def _chat(self, text: str) -> None:
        self.history.append(Message("user", text))
        system = CHAT_SYSTEM.format(profile=self.service.summary())
        messages = [Message("system", system), *self.history]
        bubble = self._say("mimir", "…")
        # When MCP tools are live and the backend supports them, run the agentic
        # loop so the model can call tools; otherwise plain streaming.
        if (self.hub and self.hub.has_tools()
                and getattr(self.llm, "supports_tools", False)):
            self._agent_worker(messages, bubble)
        else:
            self._stream_worker(messages, bubble)

    @work(thread=True)
    def _stream_worker(self, messages: list[Message], bubble: Static) -> None:
        acc: list[str] = []
        try:
            for piece in self.llm.stream(messages):
                acc.append(piece)
                self.call_from_thread(bubble.update, self._fmt("mimir", "".join(acc)))
                self.call_from_thread(self._scroll_end)
        except Exception as e:
            self.call_from_thread(bubble.update, self._fmt("error", f"LLM error: {e}"))
            return
        self.history.append(Message("assistant", "".join(acc)))

    @work(thread=True)
    def _agent_worker(self, messages: list[Message], bubble: Static) -> None:
        # `box` holds the current bubble + its accumulated text. A tool call
        # finalizes the bubble, drops a "🔧 …" line, and starts a fresh bubble
        # so streamed text lands above and below the tool announcement.
        box = {"bubble": bubble, "acc": []}

        def on_text(piece: str) -> None:
            box["acc"].append(piece)
            self.call_from_thread(box["bubble"].update,
                                  self._fmt("mimir", "".join(box["acc"])))
            self.call_from_thread(self._scroll_end)

        def on_tool(name: str, args: dict) -> None:
            preview = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
            if len(preview) > 80:
                preview = preview[:77] + "…"
            self.call_from_thread(self._say, "info", f"🔧 {name}({preview})")
            box["bubble"] = self.call_from_thread(self._say, "mimir", "…")
            box["acc"] = []

        try:
            final = self.llm.run_tools(
                messages, self.hub.tools(), self.hub.call,
                on_text=on_text, on_tool=on_tool,
            )
        except Exception as e:
            self.call_from_thread(box["bubble"].update,
                                  self._fmt("error", f"LLM error: {e}"))
            return
        self.history.append(Message("assistant", final))

    # --- ingestion (onboarding) -----------------------------------------
    @work(thread=True)
    def _ingest_worker(self, kind: str, arg: str, label: str) -> None:
        self.call_from_thread(self._say, "info", f"ingesting {label}…")
        fn = {
            "text": self.service.ingest_text,
            "file": self.service.ingest_file,
            "notion": self.service.ingest_notion,
        }[kind]
        try:
            profile_md = fn(arg)
        except Exception as e:
            self.call_from_thread(self._say, "error", f"ingest failed: {e}")
            return
        self.call_from_thread(self._say, "mimir", f"profile built → {self.config.profile_path}")
        self.call_from_thread(self._say, "info", profile_md)
        self.mode = "chat"
        self.history.clear()
        self.call_from_thread(self._say, "mimir", "Onboarding done. Ask me anything about your career.")

    # --- MCP --------------------------------------------------------------
    def _mcp_command(self, arg: str) -> None:
        """``/mcp`` status; ``/mcp add …`` adds a server; ``/mcp logout <name>``."""
        low = arg.lower()
        if low.startswith("add"):
            self._mcp_add(arg[3:].strip())
            return
        if low.startswith("logout"):
            name = arg[6:].strip()
            if not name:
                self._say("error", "usage: /mcp logout <name>")
            else:
                from infrastructure.mcp.oauth import clear_tokens
                ok = clear_tokens(name)
                self._say("info", f"cleared OAuth tokens for {name!r}" if ok
                          else f"no saved tokens for {name!r}")
            return
        if not self.mcp_servers:
            self._say(
                "info",
                "No MCP servers configured. Built-ins live in the package "
                "(resources/mcp.local.json); add your own to ~/.mimir/mcp.json, "
                "or run /mcp add <name> <cmd|url> <args…>.",
            )
            return
        if self.hub is None:
            self._say("info", "MCP hub still starting — try again in a moment.")
            return
        st = self.hub.status
        lines = [
            f"MCP: {len(st.connected)}/{len(self.mcp_servers)} connected, "
            f"{st.tool_count} tool(s) available"
        ]
        for name in st.connected:
            lines.append(f"  ✓ {name}")
        for name, err in st.failed.items():
            lines.append(f"  ✗ {name}  —  {err}")
        self._say("info", "\n".join(lines))

    def _mcp_add(self, spec: str) -> None:
        # Remote (OAuth):  /mcp add <name> <https url>
        # Local (stdio):   /mcp add <name> <command> [args…]
        parts = spec.split()
        if len(parts) < 2:
            self._say("error",
                      "usage: /mcp add <name> <command> [args…]  |  "
                      "/mcp add <name> <https-url>")
            return
        name, second, *rest = parts
        if any(s.name == name for s in self.mcp_servers):
            self._say("error", f"a server named {name!r} already exists")
            return
        if second.startswith("http://") or second.startswith("https://"):
            cfg = McpServerConfig(name=name, transport="http", url=second)
            snippet = (f"# in ~/.mimir/mcp.json (\"mcpServers\"):\n  \"{name}\": "
                       f"{{ \"type\": \"http\", \"url\": \"{second}\" }}")
            self._say("info",
                      f"added remote {name!r}; a browser may open for login…")
        else:
            args_json = json.dumps(rest)
            cfg = McpServerConfig(name=name, command=second, args=rest)
            snippet = (f"# in ~/.mimir/mcp.json (\"mcpServers\"):\n  \"{name}\": "
                       f"{{ \"command\": \"{second}\", \"args\": {args_json} }}")
            self._say("info", f"added {name!r}; reconnecting all MCP servers…")
        self.mcp_servers.append(cfg)
        self._say("info", "persist it:\n" + snippet)
        self._start_hub_worker(list(self.mcp_servers))

    @work(thread=True)
    def _start_hub_worker(self, servers: list[McpServerConfig]) -> None:
        # (Re)connect the hub. Blocking, so it runs in a worker thread.
        if self.hub is not None:
            self.hub.stop()
        hub = McpHub(servers)
        st = hub.start()
        self.hub = hub
        tools = ", ".join(t.name for t in hub.tools())
        lines = [f"MCP ready: {len(st.connected)}/{len(servers)} connected, "
                 f"{st.tool_count} tool(s)"]
        if tools:
            lines.append(f"  tools: {tools}")
        for fname, err in st.failed.items():
            lines.append(f"  ✗ {fname}  —  {err}")
        self.call_from_thread(self._say, "info", "\n".join(lines))

    # --- view helpers ----------------------------------------------------
    def _fmt(self, who: str, text: str) -> str:
        labels = {
            "user": "[b cyan]you ›[/] ",
            "mimir": "[b magenta]mimir ›[/] ",
            "info": "",
            "error": "",
        }
        return labels.get(who, "") + text

    def _say(self, who: str, text: str) -> Static:
        bubble = Static(self._fmt(who, text), classes=who, markup=True)
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(bubble)
        self._scroll_end()
        return bubble

    def _scroll_end(self) -> None:
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)


def run(config_path: str | None = None) -> None:
    config = load_config(config_path)
    MimirApp(config).run()
