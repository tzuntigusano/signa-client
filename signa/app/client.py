"""Conexión de la app con signahub. Corre en un hilo con su propio bucle asyncio y avisa a la interfaz con
señales Qt (que cruzan de hilo solas). La clave de la app vive en el llavero del sistema, nunca en un fichero."""
import asyncio
import json
import threading
import urllib.error
import urllib.request

import aiohttp
from PySide6.QtCore import QObject, Signal

from signa import __version__
from signa.core import link
from signa.core.appkey import forget_key, saved_key, store_key  # noqa: F401 (la app los usa desde aquí)

RETRY = (2, 5, 10, 30)
REPLIES = ("export", "delete_account", "trade_detail", "trade_quote", "trade_action", "risk",  # -> reply
           "close_all", "stats") + ("admin_state", "admin_invite", "admin_enable", "admin_disable",
                                      "admin_paper_only", "admin_engine", "admin_filters")
SYNC_EVERY = 300  # s: la app se pone al dia con el servidor (correcciones de calls, ajustes cambiados en otro PC)


class RegisterError(Exception):
    friendly = True  # su texto se enseña tal cual


def _http(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(link.url(path, ws=False), data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, context=link.ssl_context(), timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            raise RegisterError(json.loads(e.read()).get("error") or "Signa server error.") from None
        except ValueError:
            raise RegisterError("Signa server error.") from None
    except (urllib.error.URLError, OSError):
        raise RegisterError("Can't reach the Signa server. Check your internet connection.") from None


def terms_text(kind: str, version: int) -> str:
    """Texto (Markdown) de unas condiciones publicadas."""
    try:
        with urllib.request.urlopen(link.url(f"/terms/{kind}/{int(version)}", ws=False), context=link.ssl_context(),
                                    timeout=15) as r:
            return r.read().decode("utf-8")
    except (urllib.error.URLError, OSError):
        raise RegisterError("Can't load the terms from the Signa server right now.") from None


def send_logs(blob: bytes):
    """Settings › Diagnostics: el final de los logs de este PC, al admin de Signa."""
    req = urllib.request.Request(link.url("/support", ws=False), data=blob, method="POST",
                                 headers={"Authorization": f"Bearer {saved_key() or ''}",
                                          "Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, context=link.ssl_context(), timeout=30):
            pass
    except (urllib.error.URLError, OSError):
        raise RegisterError("Couldn't send the logs. Check your internet connection.") from None


def register(invite: str) -> str:
    """Canjea la invitación aceptando las condiciones VIGENTES y guarda la clave en el llavero."""
    terms = _http("/terms")
    res = _http("/register", {"invite": invite, "consents": terms})
    store_key(res["key"])
    return res["user"]


class Client(QObject):
    status = Signal(str)          # connecting | online | offline | rejected
    welcome = Signal(dict)        # {user, role, providers, signals} (también al sincronizar cada SYNC_EVERY s)
    signal = Signal(dict)         # una call nueva
    saved = Signal(dict)          # respuesta a save_subscription
    trades = Signal(list)         # operaciones del usuario (al cambiar alguna en el servidor)
    reply = Signal(dict)          # respuesta a request(): export | delete_account | trade_detail…
    alert = Signal(dict)          # aviso de Signa: cuadre con IBKR, error de un motor

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._req = 0
        self._stop = False
        threading.Thread(target=self.loop.run_forever, daemon=True, name="signa-client").start()

    def start(self):
        asyncio.run_coroutine_threadsafe(self._run(), self.loop)

    def stop(self):
        self._stop = True
        if self.ws is not None:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)

    def save_subscription(self, provider: str, fields: dict) -> int:
        self._req += 1
        self._send({"type": "save_subscription", "provider": provider, "fields": fields, "req": self._req})
        return self._req

    def request(self, kind: str, **fields) -> int:
        self._req += 1
        self._send({"type": kind, "req": self._req, **fields})
        return self._req

    def sync_now(self):
        if self.ws is not None and not self.ws.closed:
            asyncio.run_coroutine_threadsafe(self.ws.send_json({"type": "sync"}), self.loop)

    def _send(self, body: dict):
        if self.ws is None or self.ws.closed:
            err = {"ok": False, "error": "Not connected to the Signa server.", "req": body.get("req")}
            (self.saved if body.get("type") == "save_subscription" else self.reply).emit({**err, "type": body["type"]})
            return
        asyncio.run_coroutine_threadsafe(self.ws.send_json(body), self.loop)

    @staticmethod
    async def _sync_loop(ws):
        while not ws.closed:
            await asyncio.sleep(SYNC_EVERY)
            if not ws.closed:
                await ws.send_json({"type": "sync"})

    async def _run(self):
        attempt = 0
        while not self._stop:
            key = saved_key()
            if not key:
                self.status.emit("rejected")
                return
            self.status.emit("connecting")
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(link.url("/ws"), ssl=link.ssl_context(),
                                                                       heartbeat=30) as ws:
                    await ws.send_json({"type": "auth", "key": key, "version": __version__})
                    syncer = None
                    async for m in ws:
                        if m.type != aiohttp.WSMsgType.TEXT:
                            continue
                        d = json.loads(m.data)
                        if d.get("type") == "welcome":
                            self.ws, attempt = ws, 0
                            self.status.emit("online")
                            self.welcome.emit(d)
                            syncer = asyncio.ensure_future(self._sync_loop(ws))
                        elif d.get("type") == "sync":
                            self.welcome.emit(d)
                        elif d.get("type") == "signal":
                            self.signal.emit(d)
                        elif d.get("type") == "subscription":
                            self.saved.emit(d)
                        elif d.get("type") == "resync":  # el admin cambió algo tuyo: ponerse al día ya
                            await ws.send_json({"type": "sync"})
                        elif d.get("type") == "alert":
                            self.alert.emit(d)
                        elif d.get("type") in REPLIES:
                            self.reply.emit(d)
                        elif d.get("type") == "trades":
                            self.trades.emit(d.get("trades", []))
                    self.ws = None
                    if syncer is not None:
                        syncer.cancel()
                    if ws.close_code == 4000:  # clave revocada o usuario bloqueado
                        self.status.emit("rejected")
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                pass
            self.ws = None
            if self._stop:
                return
            self.status.emit("offline")
            await asyncio.sleep(RETRY[min(attempt, len(RETRY) - 1)])
            attempt += 1
