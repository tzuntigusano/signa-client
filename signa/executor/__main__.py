"""EJECUTOR de Signa (en el PC de cada usuario): el brazo con IBKR y con sus bots de Telegram. NO decide nada: el
motor (en el servidor) le pide «plan / quote / execute / set_stop…» y él lo hace con el broker.py de siempre.

    venv\\Scripts\\python -m signa.executor

Configuración local en %APPDATA%\\Signa\\executor.json (nada de esto sale del PC):
    {"paper": {"port": 4002, "client_id": 95},
     "live":  {"port": 4001, "client_id": 96},
     "telegram": {"hacedor": {"token": "...", "chat_id": "...", "username": "..."}}}
Los bots se enlazan desde la app (pantalla Telegram) y el ejecutor los recoge al vuelo (signa.core.bots).
La clave de la app se lee del llavero (la misma de la app). Solo se conecta a la cuenta (paper/live) que el
servidor pida para un proveedor.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from signa import __version__
from signa.core import bots, link, logs, wire
from signa.core.appkey import saved_key
from signa.core.logs import HOME, Log, single_instance

sys.path.insert(0, str(Path(__file__).with_name("copier")))
from broker import Broker, Plan, PlanError  # noqa: E402
from calls import Call, Leg  # noqa: E402
from telegram_ui import Telegram  # noqa: E402

_log = Log("executor")
LOCK_PORT = 47691
RETRY = (2, 5, 10, 30)
STATUS = HOME / "executor_status.json"  # lo lee la pantalla IBKR de la app (solo local)
SUMMARY_TAGS = {"NetLiquidation": "net_liq", "AvailableFunds": "available", "BuyingPower": "buying_power"}
DEFAULTS = {"paper": {"port": 4002, "client_id": 95}, "live": {"port": 4001, "client_id": 96}, "telegram": {}}


def log(msg: str):
    _log("executor", msg)


def load_config() -> dict:
    f = HOME / "executor.json"
    cfg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return {**DEFAULTS, **cfg}


async def port_open(port: int, timeout: float = 1.0) -> bool:
    """¿Responde el IB Gateway en ese puerto? (abrir y cerrar sin saludo: el Gateway no lo cuenta como cliente)"""
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


def account_status(cfg: dict, broker: Broker | None, gateway: bool) -> dict:
    """Estado de una cuenta para la app, con lo que ib_async ya tiene en memoria (no pide nada aquí)."""
    st = {"port": int(cfg["port"]), "client_id": int(cfg["client_id"]), "gateway": gateway,
          "used": broker is not None, "connected": bool(broker and broker.ib.isConnected())}
    if not st["connected"]:
        return st
    ib = broker.ib
    st["account"] = broker.account
    vals: dict[str, float] = {}
    for v in ib.accountValues():
        key = SUMMARY_TAGS.get(v.tag)
        if key and v.currency in ("USD", "BASE") and (key not in vals or v.currency == "USD"):
            try:
                vals[key] = float(v.value)
            except ValueError:
                pass
    st.update(vals)
    st["positions"] = sum(1 for p in ib.positions() if p.position)
    st["orders"] = len(ib.openTrades())
    return st


def protections(b: Broker) -> dict:
    """{conId: {"sl": [cantidades], "tp": [cantidades]}} de las órdenes vivas de ESTE programa, con las mismas
    funciones del broker del copiador (_our_stops / _our_tps)."""
    out = {}
    for cid in {t.contract.conId for t in b.ib.openTrades() if t.order.action == "SELL"}:
        con = type("C", (), {"conId": cid})()
        out[str(cid)] = {"sl": [t.order.totalQuantity for t in b._our_stops(con)],
                         "tp": [t.order.totalQuantity for t in b._our_tps(con)]}
    return out


def positions(b: Broker) -> dict:
    """{conId: contratos} de la cuenta según IBKR (lo que ib_async ya tiene: sin peticiones nuevas). Para el cuadre
    del servidor: la cuenta ENTERA (otros programas pueden tener más del mismo contrato)."""
    return {str(p.contract.conId): p.position for p in b.ib.positions() if p.position}


class Executor:
    def __init__(self, cfg: dict, key: str):
        self.cfg, self.key = cfg, key
        self.brokers: dict[str, Broker] = {}
        self.bots: dict[str, Telegram] = {}
        self.bot_tasks: dict[str, asyncio.Future] = {}
        self.account_subs: set[str] = set()
        self.reconnecting: set[str] = set()
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    # --- IBKR ---------------------------------------------------------------------------------------
    def broker(self, account: str) -> Broker:
        if account not in ("paper", "live"):
            raise ValueError(f"unknown account {account!r}")
        if account not in self.brokers:
            c = self.cfg[account]
            self.brokers[account] = Broker("127.0.0.1", int(c["port"]), int(c["client_id"]), live=account == "live")
        return self.brokers[account]

    async def rpc(self, method: str, a: dict):
        """Una llamada del motor. Devuelve algo que se pueda mandar en JSON."""
        if method.startswith("tg_"):
            return await self.telegram(method, a)
        if method == "diag":  # panel de admin › usuario › Status & logs: su estado y sus logs (sin secretos ni dinero)
            return await asyncio.to_thread(logs.diagnostics, bool(a.get("logs")))
        b = self.broker(a.get("account", "paper"))
        if method == "ensure":
            await b.ensure()
            return {"client_id": b.client_id}
        if method in ("plan", "next_day_plan"):
            c = wire.call_from(a["call"], Call, Leg)
            p = await (b.plan(c, a.get("suggest", True)) if method == "plan" else b.next_day_plan(c, a.get("days", 5)))
            return {"plan": wire.plan_to(p) if p else None, "call": wire.call_to(c)}
        if method == "spot":  # precio del subyacente (prueba diaria: elegir el strike). Solo lectura
            from ib_async import Index, Stock
            await b.ensure()
            sym = str(a.get("symbol", "SPY")).upper()
            con = Index(sym, "CBOE", "USD") if sym in ("SPX", "NDX", "VIX") else Stock(sym, "SMART", "USD")
            await b.ib.qualifyContractsAsync(con)
            [t] = await b.ib.reqTickersAsync(con)
            px = t.marketPrice()
            return None if px != px else float(px)  # NaN -> None
        if method == "expiry_by_price":
            r = await b.expiry_by_price(wire.call_from(a["call"], Call, Leg), a["price"], a.get("tries", 4))
            return list(r) if r else None
        plan = wire.plan_from(a["plan"], Plan) if "plan" in a else None
        if method == "quote":
            bid, ask = await b.quote(plan)
            return {"bid": bid, "ask": ask, "info": wire._plain(plan.info)}
        cb = self._callback(a.get("cb"))
        if method == "execute":
            return await b.execute(plan, a.get("limit"), on_fill=cb)
        if method == "set_stop":
            return await b.set_stop(plan, a["stop"], a.get("stop_limit", False), on_fill=cb)
        if method == "set_tp":
            return await b.set_tp(plan, a["price"], on_fill=cb)
        if method == "remove_protect":
            return await b.remove_protect(plan, a["kind"])
        if method == "executions":
            await b.ensure()
            return [{"side": f.execution.side, "client_id": getattr(f.execution, "clientId", None),
                     "shares": f.execution.shares, "price": f.execution.price, "exec_id": f.execution.execId,
                     "time": f.execution.time.isoformat() if getattr(f.execution, "time", None) else None,
                     "con_id": f.contract.conId} for f in await b.ib.reqExecutionsAsync()]
        if method == "qualify":
            await b.ensure()
            cs = [wire.contract_from(c) for c in a["contracts"]]
            await b.ib.qualifyContractsAsync(*cs)
            return [wire.contract_to(c) for c in cs]
        raise ValueError(f"unknown method {method}")

    def _callback(self, cb: int | None):
        """La orden se llena MÁS TARDE: se avisa al motor con el mismo id."""
        if cb is None:
            return None

        def fire(*args):
            if self.ws is not None and not self.ws.closed:
                asyncio.ensure_future(self.ws.send_json({"type": "cb", "cb": cb, "args": wire._plain(list(args))}))
        return fire

    async def ticks(self):
        """Cada segundo, bid/ask de todo lo suscrito (el motor lo lee al instante con peek()). Y cada 2 s, los SL y
        TP vivos de este programa por contrato: con eso el motor sabe si a una operación le falta su protección
        (_heal la repone; sin esto, a través del puente creía que siempre la tenía)."""
        n = 0
        while True:
            await asyncio.sleep(1)
            n += 1
            if self.ws is None or self.ws.closed:
                continue
            for account, b in self.brokers.items():
                items = {}
                for cid, t in list(b._subs.items()) + list(b._hot.items()):
                    bid, ask = wire._plain(t.bid), wire._plain(t.ask)
                    if bid is not None or ask is not None:
                        items[str(cid)] = [bid, ask]
                if items:
                    await self.ws.send_json({"type": "ticks", "account": account, "items": items})
                if n % 2 == 0 and b.ib.isConnected():
                    st = account_status(self.cfg[account], b, True)
                    await self.ws.send_json({"type": "orders", "account": account, "items": protections(b),
                                             "positions": positions(b), "net_liq": st.get("net_liq")})

    # --- Telegram -----------------------------------------------------------------------------------
    async def telegram(self, method: str, a: dict):
        bot = self.bots.get(a.get("provider"))
        if bot is None:
            return None  # sin bot para ese proveedor: el motor sigue igual
        if method == "tg_send":
            return await bot.send(a["text"], buttons=a.get("buttons"), force_reply=a.get("force_reply", False),
                                  silent=a.get("silent", False), html=a.get("html", False))
        if method == "tg_edit":
            return await bot.edit(a["message_id"], a["text"], buttons=a.get("buttons"), html=a.get("html", False),
                                  live=a.get("live", False))
        if method == "tg_delete":
            return await bot.delete(a["message_id"])
        raise ValueError(f"unknown method {method}")

    def sync_bots(self, wanted: dict[str, dict]):
        """Deja corriendo exactamente los bots de `wanted` ({proveedor: {token, chat_id}}). Se llama al arrancar y
        cada vez que la app cambia executor.json (enlazar / quitar un bot en la pantalla Telegram)."""
        for provider in list(self.bots):
            t = wanted.get(provider)
            bot = self.bots[provider]
            if t and bot.base.endswith(f"{t['token']}/") and bot.chat_id == str(t["chat_id"]):
                continue
            self.bot_tasks.pop(provider).cancel()
            del self.bots[provider]
            if bot.session is not None:
                asyncio.ensure_future(bot.session.close())
            log(f"Telegram bot for {provider} stopped")
        for provider, t in wanted.items():
            if provider in self.bots:
                continue
            bot = Telegram(t["token"], str(t["chat_id"]))

            async def on_button(action, cid, msg_id, p=provider):
                await self._send({"type": "tg_button", "provider": p, "action": action, "cid": cid, "msg_id": msg_id})

            async def on_text(text, reply_to, p=provider):
                await self._send({"type": "tg_text", "provider": p, "text": text})
            bot.on_button, bot.on_text = on_button, on_text
            self.bots[provider] = bot
            self.bot_tasks[provider] = asyncio.ensure_future(bot.poll())
            log(f"Telegram bot for {provider} ready")

    async def watch_bots(self, every: float = 3):
        last = None
        while True:
            wanted = bots.linked()
            if wanted != last:
                self.sync_bots(wanted)
                last = wanted
            await asyncio.sleep(every)

    async def _send(self, d: dict):
        if self.ws is not None and not self.ws.closed:
            await self.ws.send_json(d)

    # --- servidor -----------------------------------------------------------------------------------
    async def handle(self, d: dict):
        try:
            res = await self.rpc(d["method"], d.get("args") or {})
            await self._send({"type": "rpc_result", "id": d["id"], "ok": True, "result": wire._plain(res)})
        except PlanError as e:
            await self._send({"type": "rpc_result", "id": d["id"], "ok": False, "etype": "PlanError",
                              "msg": str(e), "suggest": getattr(e, "suggest", None)})
        except Exception as e:
            log(f"{d.get('method')}: {type(e).__name__}: {e}")
            await self._send({"type": "rpc_result", "id": d["id"], "ok": False, "etype": type(e).__name__,
                              "msg": str(e)})

    async def status(self) -> dict:
        accounts = {}
        for name in ("paper", "live"):
            b = self.brokers.get(name)
            if b is None or not b.ib.isConnected():
                self.account_subs.discard(name)  # al reconectar hay que volver a suscribirse
            elif name not in self.account_subs:  # el broker del copiador solo trae posiciones: una suscripción de
                self.account_subs.add(name)      # lectura a los valores de la cuenta (net liq, disponible)
                asyncio.ensure_future(b.ib.reqAccountUpdatesAsync(b.account))
            gw = await port_open(int(self.cfg[name]["port"]))
            if b is not None and not b.ib.isConnected() and gw and name not in self.reconnecting:
                asyncio.ensure_future(self.reconnect(name, b))  # p. ej. tras reiniciar el Gateway
            accounts[name] = account_status(self.cfg[name], self.brokers.get(name), gw)
        return {"at": datetime.now(timezone.utc).isoformat(), "server": self.ws is not None and not self.ws.closed,
                "accounts": accounts, "bots": sorted(self.bots)}

    async def reconnect(self, name: str, b: Broker):
        """Como hace el copiador: si se cae la conexión con una cuenta en uso y su Gateway vuelve, se reconecta ya
        (no en la próxima orden, que pagaría la espera)."""
        self.reconnecting.add(name)
        try:
            await b.ensure()
            self.account_subs.discard(name)  # nueva conexión: volver a suscribir los valores de la cuenta
            log(f"IBKR {name} reconnected ({b.account})")
        except Exception as e:
            log(f"IBKR {name} reconnect failed: {type(e).__name__}: {e}")
        finally:
            self.reconnecting.discard(name)

    async def status_loop(self, every: float = 10):
        while True:
            try:
                tmp = STATUS.with_suffix(".tmp")
                tmp.write_text(json.dumps(await self.status()), encoding="utf-8")
                tmp.replace(STATUS)
            except Exception as e:  # el estado es informativo: nunca tumba al ejecutor
                log(f"status: {type(e).__name__}: {e}")
            await asyncio.sleep(every)

    async def run(self):
        asyncio.ensure_future(self.status_loop())
        asyncio.ensure_future(self.watch_bots())
        asyncio.ensure_future(self.ticks())
        attempt = 0
        while True:
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(
                        link.url("/exec"), ssl=link.ssl_context(), heartbeat=30, max_msg_size=4 * 1024 * 1024) as ws:
                    await ws.send_json({"type": "auth", "key": self.key, "version": __version__})
                    hello = await ws.receive_json(timeout=15)
                    if hello.get("type") != "welcome":
                        raise ConnectionError(f"rejected: {hello}")
                    self.ws, attempt = ws, 0
                    log(f"connected to signahub as {hello.get('user')}")
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            d = json.loads(m.data)
                            if d.get("type") == "rpc":  # en paralelo: una orden no espera a un precio
                                asyncio.ensure_future(self.handle(d))
                    self.ws = None
                    if ws.close_code == 4000:
                        log("the server rejected this app key: stopping")
                        return
            except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError, OSError, TypeError) as e:
                self.ws = None
                wait = RETRY[min(attempt, len(RETRY) - 1)]
                attempt += 1
                log(f"server unreachable ({type(e).__name__}: {e}); retry in {wait}s")
                await asyncio.sleep(wait)


def main() -> int:
    lock = single_instance(LOCK_PORT)
    if lock is None:
        log("another executor is already running on this PC: exiting")
        return 3
    key = saved_key()
    if not key:
        log("no Signa app key on this PC: open the app and sign in first")
        return 1
    try:
        asyncio.run(Executor(load_config(), key).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
