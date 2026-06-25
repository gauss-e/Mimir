"""Mimir TUI — a Claude-Code-style career advisor.

Full-screen terminal app: a scrolling conversation pane with a docked input box
at the bottom and a status footer. On startup it checks for a populated
``profile.md``; if found it goes straight to chat, otherwise it walks the user
through onboarding (paste a summary, ``/file`` a résumé, or ``/notion``).
"""

from __future__ import annotations

import json
import os

# Disable Textual's Kitty keyboard protocol BEFORE importing textual: under
# `report-all-keys`, a Kitty-capable terminal (kitty/ghostty/WezTerm/foot,
# recent iTerm2/Konsole) reports every keypress as a CSI-u sequence instead of
# plain text. That breaks CJK/IME input — an IME commit arrives as a "text
# event" (`CSI 0;;<codepoints>u`) whose multi-codepoint form Textual's parser
# can't decode, so composed Chinese silently vanishes; even plain space/slash
# come through with no character. Legacy mode delivers IME-composed text as raw
# UTF-8, which Textual parses correctly. `textual.constants` reads this env var
# at import time, so it must be set first. (Respect an explicit user override.)
os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Header, Input, Static

from banner_art import MIMIR_ART
from config import Config, load_config
from infrastructure.context import ContextBuilder
from infrastructure.llm import Message, build_provider
from infrastructure.mcp import McpHub, McpServerConfig, load_servers
from profiles import ProfileService, ProfileStore

# Mimir's proactive opening lines. The agent loads everything on entry and
# speaks first, in-voice — no raw onboarding screen. GREET_NEW is shown when no
# profile exists yet; GREET_BACK when one is already on file.
GREET_NEW = (
    "我醒了。可惜此刻我对你还一无所知 —— 你做过什么、擅长什么、想去哪里，"
    "于我都还是一片空白，朋友。\n\n"
    "随口跟我聊聊你自己吧。也可以把简历交给我（/file <路径>），"
    "或让我从你的 Notion 里取（/notion <关键词>）。"
)
GREET_BACK = "我在。你的画像我已记得 —— 想从哪儿聊起？"

HELP = ("/profile  /reload  /file <path>  /notion <q>  "
        "/mcp [add <name> <cmd|url> …] [logout <name>]  /help  /quit")

# MIMIR_ART (imported above): pixel-art Mimir (God of War), truecolor
# half-block portrait shown left of the welcome banner. See banner_art.py.


def _mimir_version() -> str:
    try:
        from importlib.metadata import version
        return version("mimir")
    except Exception:
        return "0.1.0"


# Jarvis cockpit palette: deep navy base, cyan (user) + amber (mimir) accents,
# violet for tool calls. Drives both the theme vars used in CSS and the chips.
JARVIS_THEME = Theme(
    name="jarvis",
    primary="#38bdf8",    # cyan  — user
    secondary="#fbbf24",  # amber — mimir
    accent="#a78bfa",     # violet — tool calls
    foreground="#e6edf3",
    background="#0b0e14",
    surface="#11161f",
    panel="#161d2b",
    success="#34d399",
    warning="#fbbf24",
    error="#f85149",
    dark=True,
)


class MimirApp(App):
    CSS = """
    Screen { layout: vertical; background: $background; }
    /* Chat fills all space between header and the input/footer. */
    #chat { height: 1fr; padding: 1 2; background: $background; }
    /* Every message is a card: bg fill, a role-tinted left bar, inset padding. */
    #chat > Static { width: 100%; height: auto; margin: 0 0 1 0; padding: 0 1; }
    /* Welcome banner: a rounded box, fixed above the chat (outside the scroll)
       so it stays put. Hugs its content (Claude-Code style). */
    #banner { width: auto; height: auto; border: round $secondary;
              padding: 0 1; margin: 1 1 0 1; color: $text; }
    .user  { background: $primary 12%;   border-left: thick $primary; }
    .mimir { background: $panel;          border-left: thick $secondary; }
    /* Tool calls: dimmer inset card, indented under the reply that triggered them. */
    .tool  { background: $surface; color: $text-muted; border-left: thick $accent;
             margin: 0 2 1 6; }
    .info  { color: $text-muted; text-style: italic; }
    .error { background: $error 12%; color: $error; border-left: thick $error; }
    /* Input sits in normal flow (not docked) above the footer, fixed height,
       so it never fights the Footer for the bottom edge or clip the chat. */
    #prompt { height: 3; margin: 1 1 0 1; border: round $secondary; background: $surface; }
    #prompt:focus { border: round $primary; }
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
        self.context: ContextBuilder | None = None
        self.llm = None
        self.init_error: str | None = None
        self.mode = "chat"  # "chat" | "onboarding"
        self.history: list[Message] = []
        # MCP servers read entirely from JSON (nothing hardcoded): built-in
        # resources/mcp.local.json overlaid by ~/.mimir/mcp.json. The hub keeps
        # them connected so the agent can call their tools mid-chat.
        self.mcp_servers = load_servers(config.mcp_json)
        self.hub: McpHub | None = None
        self.banner: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        # Banner is fixed above the chat (not inside the scroll) so it stays put.
        yield Static(id="banner", markup=True)
        yield VerticalScroll(id="chat")
        yield Input(id="prompt", placeholder="Message Mimir…  (/help, /quit)")
        yield Footer()

    # --- lifecycle -------------------------------------------------------
    def on_mount(self) -> None:
        self.register_theme(JARVIS_THEME)
        self.theme = "jarvis"
        self.title = "✦ Mimir ✦"
        self.sub_title = ""
        n = len(self.mcp_servers)
        self._mount_banner(f"[dim]connecting {n} MCP…[/]" if n
                           else "[dim]no MCP servers[/]")
        try:
            self.llm = build_provider(self.config.provider, self.config.llm_settings())
            self.service = ProfileService(self.store, self.llm, self.config.notion)
        except Exception as e:
            self.init_error = str(e)
            self._say("error", f"LLM not ready: {e}")
            self._say("info", "Fix config.toml (or env keys) and restart.")
            return

        # System prompt + context are built and ready from the moment we mount,
        # rebuilt fresh each turn (it reads the hub live, so MCP tools join in as
        # they connect). Mimir then speaks first, in-voice — never an onboarding
        # screen: an empty profile becomes a warm invitation, not a checklist.
        self.context = ContextBuilder(self.service, self.hub)
        if self.service.needs_onboarding():
            self.mode = "onboarding"
            self._say("mimir", GREET_NEW)
        else:
            self.mode = "chat"
            self._say("mimir", GREET_BACK)
        if self.mcp_servers:
            # Connect quietly in the background — no tool-count chatter in the UI.
            self._start_hub_worker(self.mcp_servers)
        self.query_one(Input).focus()

    def on_unmount(self) -> None:
        # Fire-and-forget: MCP teardown (esp. a remote session's DELETE round-
        # trip) must not block quitting. Hand it to a daemon thread and return.
        if self.hub is not None:
            self.hub.shutdown_background()

    # --- input handling --------------------------------------------------
    # Some terminals deliver certain keys with no associated character (e.g.
    # "/" arrives as key="slash" character=None). Textual's Input only inserts
    # keys that carry a printable character, so those keys silently vanish. We
    # recover them here: when a focused prompt sees such a key, insert the glyph.
    _KEY_FIXUPS = {"slash": "/", "question_mark": "?", "backslash": "\\",
                   "space": " "}

    def on_key(self, event) -> None:
        if event.character is not None:
            return
        glyph = self._KEY_FIXUPS.get(event.key)
        if glyph is None:
            # A CJK IME may commit composed text as the key *name* with no
            # character (e.g. key="你好", character=None); Textual's Input only
            # inserts events that carry a printable character, so the Chinese
            # silently vanishes. Recover it: a key whose name is non-ASCII and
            # printable is composed text, not a control-key name like "enter"
            # or "space" (those are ASCII and stay out of this branch).
            if event.key and not event.key.isascii() and event.key.isprintable():
                glyph = event.key
            else:
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
            self._say("info", HELP)
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
        messages = self.context.messages(self.history)
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
        # finalizes the bubble, drops a dim "(Mimir is working…)" line — we
        # never expose raw tool names/args — and starts a fresh bubble so
        # streamed text lands above and below the working note.
        box = {"bubble": bubble, "acc": []}

        def on_text(piece: str) -> None:
            box["acc"].append(piece)
            self.call_from_thread(box["bubble"].update,
                                  self._fmt("mimir", "".join(box["acc"])))
            self.call_from_thread(self._scroll_end)

        def on_tool(name: str, args: dict) -> None:
            self.call_from_thread(self._say, "info", "(Mimir 正在查阅资料…)")
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
        self.call_from_thread(self._say, "info", f"(Mimir 正在读取 {label}…)")
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
        self.call_from_thread(self._say, "mimir", "记下了 —— 我正把它整理进对你的认识里。")
        self.call_from_thread(self._say, "info", profile_md)
        self.mode = "chat"
        self.history.clear()
        self.call_from_thread(self._say, "mimir", "好了，我对你已经有了初步的画像。接着聊吧。")

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
        if self.context is not None:
            self.context.hub = hub
        ok, total = len(st.connected), len(servers)
        # Banner shows connection status only — no tool counts in the UI.
        mcp_line = (f"[#34d399]✓[/] connected {ok}/{total} MCP"
                    if ok else f"[#f85149]✗[/] 0/{total} MCP — see /mcp")
        self.call_from_thread(self._render_banner, mcp_line)
        # Only surface failures; a clean connect stays silent.
        if st.failed:
            lines = [f"  ✗ {fname}  —  {err}" for fname, err in st.failed.items()]
            self.call_from_thread(self._say, "error", "\n".join(lines))

    # --- banner ----------------------------------------------------------
    def _mount_banner(self, mcp_line: str) -> None:
        banner = self.query_one("#banner", Static)
        banner.border_title = "Mimir"
        self.banner = banner
        self._render_banner(mcp_line)

    def _render_banner(self, mcp_line: str) -> None:
        """Icon (left) + info rows (right), Claude-Code style. ``mcp_line`` is
        the live MCP status, refreshed once the hub connects."""
        if self.banner is None:
            return
        info = [
            f"[b #fbbf24]Mimir[/]  [dim]v{_mimir_version()}[/]",
            "[dim]God of War · the well of wisdom[/]",
            mcp_line,
            "[dim]/help for commands · /quit to exit[/]",
        ]
        # Vertically center the info block against the taller portrait.
        pad = (len(MIMIR_ART) - len(info)) // 2
        info = [""] * pad + info + [""] * (len(MIMIR_ART) - len(info) - pad)
        rows = [f"{art}      {tx}" for art, tx in zip(MIMIR_ART, info)]
        self.banner.update("\n".join(rows))

    # --- view helpers ----------------------------------------------------
    def _fmt(self, who: str, text: str) -> str:
        # Filled chips: dark glyph on the role's accent color. Chip lands on the
        # first line; multi-line bodies flow underneath inside the card.
        labels = {
            "user":  "[b #0b0e14 on #38bdf8] YOU [/]  ",
            "mimir": "[b #0b0e14 on #fbbf24] MIMIR [/]  ",
            "tool":  "[b #0b0e14 on #a78bfa] TOOL [/]  ",
            "error": "[b #0b0e14 on #f85149] ERROR [/]  ",
            "info":  "",
        }
        return labels.get(who, "") + text

    def _say(self, who: str, text: str) -> Static:
        bubble = Static(self._fmt(who, text), classes=who, markup=True)
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(bubble)
        self._scroll_end()
        # Keep the cursor anchored in the input: mounting messages (or a click)
        # can move focus, so re-assert it on the prompt after every message.
        self.query_one("#prompt", Input).focus()
        return bubble

    def _scroll_end(self) -> None:
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)


def run(config_path: str | None = None) -> None:
    config = load_config(config_path)
    MimirApp(config).run()
