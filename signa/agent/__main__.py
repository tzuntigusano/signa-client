"""AGENTE de fuente (solo en el PC de Ivan): notificaciones de Windows -> signahub.

    venv\\Scripts\\python -m signa.agent           (Ctrl+C para parar)
    venv\\Scripts\\python -m signa.agent --check   muestra qué notificaciones llegan y a qué proveedor irían, sin mandar

Configuración local en %APPDATA%\\Signa\\agent.json (NO está en el repositorio: allí van la clave y qué
notificaciones son de cada proveedor):
    {"key": "ag_…",
     "sources": [{"provider": "hacedor", "app": "discord", "channel_re": "…", "server_re": "…"}]}
No interpreta nada: manda el texto tal cual; interpretar y decidir es cosa del servidor.
"""
import asyncio
import json
import os
import re
import sys
from datetime import timezone
from pathlib import Path

import aiohttp

from signa.core import link
from signa.core.logs import Log, single_instance

from .notifications import Notice, NotPermitted, listen
from .outbox import Outbox
from .recorder import Recorder

HOME = Path(os.environ.get("APPDATA", Path.home())) / "Signa"
RETRY = (2, 5, 10, 30, 60)


_log = Log("agent")
LOCK_PORT = 47690  # un solo agente por PC


def log(msg: str):
    _log("agent", msg)


def load_config() -> dict:
    cfg = json.loads((HOME / "agent.json").read_text(encoding="utf-8"))
    for s in cfg.get("sources", []):
        s["_ch"] = re.compile(s.get("channel_re") or ".", re.I)
        s["_sv"] = re.compile(s.get("server_re") or ".", re.I)
    return cfg


def notice_ref(notif_id: int, at) -> str:
    """Ref estable de una notificación: su id de Windows + el día (UTC). La misma que usa el volcado del histórico
    del copiador (que guardó el mismo id), así lo de antes y lo de ahora no se duplica."""
    return f"n{notif_id}-{at.astimezone(timezone.utc):%Y%m%d}"


def match(cfg: dict, n: Notice) -> str | None:
    """¿De qué proveedor es esta notificación? (None = de nadie: se ignora y NO sale del PC)."""
    for s in cfg.get("sources", []):
        if s.get("app", "").lower() in n.app.lower() and s["_ch"].search(n.channel) and s["_sv"].search(n.server):
            return s["provider"]
    return None


class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.outbox = Outbox(HOME / "agent_outbox.db")
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.wake = asyncio.Event()
        rc = cfg.get("recorder")  # {"port": 4002, "client_id": 91}: grabar precios con el IB Gateway de este PC
        self.recorder = Recorder(self._send_now, rc.get("port", 4002), rc.get("client_id", 91)) if rc else None

    async def read(self):
        async for n in listen():
            provider = match(self.cfg, n)
            if not provider or not n.text:
                continue
            ref = notice_ref(n.id, n.at)
            self.outbox.put(ref, {"type": "notification", "provider": provider, "text": n.text,
                                  "ts": n.at.isoformat(timespec="milliseconds"), "ref": ref})
            log(f"{provider}: {n.text[:70]!r} ({len(self.outbox)} pending)")
            self.wake.set()

    async def send(self):
        """Conecta (con reintentos) y vacía la bandeja; los acks del servidor borran lo confirmado."""
        attempt = 0
        while True:
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(link.url("/agent"), ssl=link.ssl_context(),
                                                                       heartbeat=30) as ws:
                    await ws.send_json({"type": "auth", "key": self.cfg["key"]})
                    hello = await ws.receive_json(timeout=15)
                    if hello.get("type") != "welcome":
                        raise ConnectionError(f"rejected: {hello}")
                    log("connected to signahub")
                    self.ws, attempt = ws, 0
                    if self.recorder:
                        await ws.send_json({"type": "recorder"})  # pide qué contratos grabar
                    acks = asyncio.create_task(self._acks(ws))
                    sent: set[str] = set()  # mandados por ESTA conexión y aún sin ack (no se repiten)
                    try:
                        while not ws.closed:
                            for body in self.outbox.pending():
                                if body["ref"] not in sent:
                                    await ws.send_json(body)
                                    sent.add(body["ref"])
                            self.wake.clear()
                            await asyncio.wait([asyncio.ensure_future(self.wake.wait()), acks], timeout=30,
                                               return_when=asyncio.FIRST_COMPLETED)
                            if acks.done():
                                break
                    finally:
                        acks.cancel()
                        self.ws = None
            except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError, OSError, TypeError) as e:
                wait = RETRY[min(attempt, len(RETRY) - 1)]
                attempt += 1
                log(f"server unreachable ({type(e).__name__}: {e}); retry in {wait}s, {len(self.outbox)} pending")
                await asyncio.sleep(wait)

    async def _acks(self, ws):
        async for m in ws:
            if m.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(m.data)
                if d.get("type") == "ack" and d.get("ref"):
                    self.outbox.done(d["ref"])
                elif d.get("type") == "tracks" and self.recorder:
                    self.recorder.set_tracks(d.get("items") or [])
                elif d.get("type") == "track" and self.recorder:
                    self.recorder.track(d)

    async def _send_now(self, body: dict):
        """Precios de la grabadora: van directos (si no hay conexión se pierde esa muestra, llega la siguiente)."""
        if self.ws is not None and not self.ws.closed:
            await self.ws.send_json(body)

    async def run(self):
        await asyncio.gather(self.read(), self.send(), *([self.recorder.run()] if self.recorder else []))


async def check(cfg: dict):
    log("listening; nothing is sent (Ctrl+C to stop)")
    async for n in listen():
        log(f"[{n.app}] {n.author} | {n.channel} | {n.server} -> {match(cfg, n) or '(ignored)'}: {n.text[:60]!r}")


def main() -> int:
    lock = single_instance(LOCK_PORT) if "--check" not in sys.argv else True
    if lock is None:
        log("another agent is already running on this PC: exiting")
        return 3
    try:
        cfg = load_config()
        asyncio.run(check(cfg) if "--check" in sys.argv else Agent(cfg).run())
    except NotPermitted as e:
        log(str(e))
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
