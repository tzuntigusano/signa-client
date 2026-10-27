"""Pedir acceso con Discord (pantalla Welcome): abre el login de Discord en el navegador, recoge la respuesta en
http://127.0.0.1:53682/discord (solo este PC) y manda el código al servidor, que es quien lo canjea con el secreto
de la aplicación y deja la solicitud pendiente para el admin. Aquí no se guarda nada de Discord."""
import http.server
import secrets
import threading
import urllib.parse
import webbrowser

from .client import RegisterError, _http

WAIT = 300  # s para terminar el login en el navegador
PAGE = ("<!doctype html><meta charset=utf-8><title>Signa</title><body style='font-family:system-ui;background:#111;"
        "color:#eee;display:grid;place-items:center;height:90vh'><div><h2>{title}</h2><p>{text}</p></div>")


def config() -> dict:
    """{enabled, client_id, redirect_uri} del servidor (sin la aplicación de Discord configurada, enabled=False)."""
    return _http("/access/config")


def authorize_url(cfg: dict, state: str) -> str:
    q = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "response_type": "code",
         "scope": "identify", "state": state, "prompt": "none"}
    return "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(q)


def wait_for_code(cfg: dict, open_browser=webbrowser.open, timeout: float = WAIT) -> str:
    """Abre el login y espera a que Discord vuelva a 127.0.0.1 con el código (comprobando el state)."""
    target = urllib.parse.urlparse(cfg["redirect_uri"])
    state, got = secrets.token_urlsafe(16), {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            if url.path != target.path or q.get("state") != state:
                self.send_response(404)
                self.end_headers()
                return
            got.update(q)
            ok = "code" in q
            body = PAGE.format(title="Done" if ok else "Cancelled",
                               text="Go back to Signa." if ok else "You can close this tab and try again in Signa.")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_a):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", target.port or 80), Handler)
    except OSError:
        raise RegisterError("Another Signa window is already waiting for Discord. Close it and try again.") from None
    server.timeout = 1
    try:
        open_browser(authorize_url(cfg, state))
        for _ in range(int(timeout)):
            server.handle_request()
            if got:
                break
    finally:
        server.server_close()
    if "code" not in got:
        raise RegisterError("Discord login was cancelled or took too long." if got else
                            "Discord login took too long. Try again.")
    return got["code"]


def request_access(code: str) -> dict:
    """{status: pending|approved|rejected|member, handle}"""
    return _http("/access/request", {"code": code})


def run(done, failed):
    """Todo el proceso en un hilo (no bloquea la ventana): done(resultado) o failed(texto)."""
    def work():
        try:
            cfg = config()
            if not cfg.get("enabled"):
                raise RegisterError("Requests with Discord aren't open yet. Ask the admin for an invite.")
            done(request_access(wait_for_code(cfg)))
        except RegisterError as e:
            failed(str(e))
    threading.Thread(target=work, daemon=True, name="discord-login").start()
