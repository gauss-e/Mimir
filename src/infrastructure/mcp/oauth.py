"""OAuth for remote MCP servers — Notion's hosted server, etc.

Remote servers (``mcp.notion.com/mcp``) use OAuth 2.0 SSO instead of a static
token, exactly like Claude Code: you log in once in the browser and the tokens
are cached and refreshed for you. We don't hand-roll the flow — the MCP SDK's
``OAuthClientProvider`` does PKCE + dynamic client registration + refresh. We
only supply the two human-facing pieces:

* a **redirect handler** that opens the browser, and
* a **callback handler** — a one-shot ``localhost`` web server that catches the
  redirect and hands the auth code back to the SDK.

Tokens persist in ``~/.mimir/oauth/<server>.json`` (mode 600), so re-launches
skip the login. (A keychain-backed store could replace this later.)
"""

from __future__ import annotations

import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Loopback redirect (RFC 8252). The port is a free ephemeral one, chosen once
# per server at first login and persisted in the token store: it must stay
# stable across logins so a cached dynamic-client registration keeps matching.
CALLBACK_HOST = "localhost"
# Legacy fixed port. Installs already registered with it (pre-free-port) have no
# stored port, so they keep using 8765 — switching would break their cached
# registration. Only fresh logins get a free port.
LEGACY_PORT = 8765

_STORE_DIR = Path.home() / ".mimir" / "oauth"

_DONE_HTML = (
    b"<html><body><h2>Mimir is connected.</h2>"
    b"<p>You can close this tab and return to the terminal.</p></body></html>"
)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _free_port() -> int:
    """Ask the OS for a free TCP port (bind to 0, read the assigned port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((CALLBACK_HOST, 0))
        return s.getsockname()[1]


def _redirect_port(server_name: str) -> int:
    """The loopback port for this server, stable across logins.

    Returns the port saved in the token store. If none is saved: a legacy
    install (one that already has a cached client registration) stays on
    ``LEGACY_PORT`` so its registered redirect keeps matching; a fresh server
    gets a new free port, which is persisted for next time.
    """
    path = _STORE_DIR / f"{_safe(server_name)}.json"
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    port = data.get("redirect_port")
    if port:
        return port
    port = LEGACY_PORT if data.get("client_info") else _free_port()
    data["redirect_port"] = port
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return port


class FileTokenStorage:
    """SDK ``TokenStorage``: cache tokens + client registration on disk (600)."""

    def __init__(self, server_name: str):
        self._path = _STORE_DIR / f"{_safe(server_name)}.json"

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        _STORE_DIR.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))
        self._path.chmod(0o600)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


def _wait_for_callback(port: int, timeout: float) -> tuple[str, str | None]:
    """Run a one-shot loopback server; return (code, state) from the redirect."""
    box: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            box["code"] = (qs.get("code") or [""])[0]
            box["state"] = (qs.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_DONE_HTML)

        def log_message(self, *_):  # silence stderr access logs
            pass

    server = HTTPServer((CALLBACK_HOST, port), Handler)
    server.timeout = timeout
    try:
        server.handle_request()  # blocks until one request (or timeout)
    finally:
        server.server_close()
    if not box.get("code"):
        raise TimeoutError("no OAuth callback received (login timed out)")
    return box["code"], box.get("state")


def build_oauth_provider(server_url: str, server_name: str, login_timeout: float = 300.0):
    """Return an ``OAuthClientProvider`` wired to a browser + loopback callback."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    port = _redirect_port(server_name)
    redirect_uri = f"http://{CALLBACK_HOST}:{port}/callback"

    async def redirect_handler(auth_url: str) -> None:
        # Opening the browser is the user-visible "please log in" signal.
        webbrowser.open(auth_url)

    async def callback_handler() -> tuple[str, str | None]:
        import anyio

        # The loopback server blocks, so run it off the event loop.
        return await anyio.to_thread.run_sync(_wait_for_callback, port, login_timeout)

    metadata = OAuthClientMetadata(
        client_name="Mimir",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",  # public client + PKCE
    )
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=FileTokenStorage(server_name),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


def clear_tokens(server_name: str) -> bool:
    """Forget cached OAuth state for a server (forces re-login). Returns removed."""
    path = _STORE_DIR / f"{_safe(server_name)}.json"
    if path.exists():
        path.unlink()
        return True
    return False
