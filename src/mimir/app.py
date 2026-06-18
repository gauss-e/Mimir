"""Mimir TUI — a Claude-Code-style career advisor.

Full-screen terminal app: a scrolling conversation pane with a docked input box
at the bottom and a status footer. On startup it checks for a populated
``profile.md``; if found it goes straight to chat, otherwise it walks the user
through onboarding (paste a summary, ``/file`` a résumé, or ``/notion``).
"""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from .config import Config, load_config
from .llm import Message, build_provider
from .profile import ProfileService, ProfileStore

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

HELP = "/profile  /reload  /file <path>  /notion <q>  /help  /quit"


class MimirApp(App):
    CSS = """
    #chat { height: 1fr; padding: 0 1; }
    #chat > Static { margin: 0 0 1 0; }
    .user   { color: $text; }
    .mimir  { color: $success; }
    .info   { color: $text-muted; }
    .error  { color: $error; }
    Input { dock: bottom; border: round $accent; }
    """
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
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
        self.query_one(Input).focus()

    # --- input handling --------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one(Input).clear()
        if not text:
            return
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
