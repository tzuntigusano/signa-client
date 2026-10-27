"""GRABADORA de precios (solo en el PC de Ivan): bid/ask de CADA contrato que mande cualquier proveedor, cada pocos
segundos mientras esté vivo, para tener la curva completa de cada call y poder simular.

Qué grabar lo dice el servidor (sus entradas de los últimos días que no han vencido). Conexión a IB Gateway
SIEMPRE en solo lectura (readonly=True) y con su propio id de cliente: no puede mandar ni cancelar órdenes."""
import asyncio
import datetime as dt
import math
from zoneinfo import ZoneInfo

from signa.core.logs import Log

NY = ZoneInfo("America/New_York")
SAMPLE = 5          # s entre muestras
HEARTBEAT = 60      # s: aunque no cambie, un punto por minuto (para saber que seguía igual)
OPEN, CLOSE = dt.time(9, 30), dt.time(16, 15)


_log = Log("agent")


def log(msg: str):
    _log("recorder", msg)


def market_open(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(NY)
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def clean(v) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or v < 0 else round(float(v), 4)


class Recorder:
    def __init__(self, send, port: int = 4002, client_id: int = 91, host: str = "127.0.0.1"):
        self.send = send  # corrutina: manda un dict al servidor
        self.host, self.port, self.client_id = host, port, client_id
        self.want: dict[tuple, int] = {}      # contrato -> id de la señal (la entrada) a la que van los precios
        self.tickers: dict[tuple, object] = {}
        self.last_sent: dict[tuple, tuple] = {}
        self.ib = None

    # --- qué grabar (lo manda el servidor) -----------------------------------------------------
    def set_tracks(self, items: list[dict]):
        for it in items:
            self.track(it)

    def track(self, it: dict):
        c = it.get("contract")
        if not c or not all(c[:4]):
            return
        key = (str(c[0]).upper(), float(c[1]), str(c[2]).upper(), str(c[3]))
        if key[3] < dt.datetime.now(NY).strftime("%Y%m%d"):
            return  # ya vencido
        self.want.setdefault(key, int(it["signal_id"]))

    # --- IBKR ----------------------------------------------------------------------------------
    async def _connect(self):
        from ib_async import IB
        self.ib = IB()
        await self.ib.connectAsync(self.host, self.port, clientId=self.client_id, readonly=True, timeout=15)
        self.ib.reqMarketDataType(1)
        self.tickers.clear()
        log(f"connected to IB Gateway {self.port} (read only, client {self.client_id})")

    async def _subscribe(self, key: tuple):
        from ib_async import Option
        sym, strike, right, expiry = key
        opt = Option(sym, expiry, strike, right, "SMART", currency="USD", tradingClass="SPXW" if sym == "SPX" else "")
        q = await self.ib.qualifyContractsAsync(opt)
        if not q or not getattr(q[0], "conId", 0):
            log(f"unknown contract {sym} {strike:g}{right} {expiry}: not recorded")
            self.want.pop(key, None)
            return
        self.tickers[key] = self.ib.reqMktData(q[0], "", False, False)
        log(f"recording {sym} {strike:g}{right} {expiry}")

    def _drop_expired(self):
        today = dt.datetime.now(NY).strftime("%Y%m%d")
        after_close = dt.datetime.now(NY).time() > CLOSE
        for key in [k for k in self.want if k[3] < today or (k[3] == today and after_close)]:
            self.want.pop(key, None)
            t = self.tickers.pop(key, None)
            if t is not None and self.ib is not None:
                self.ib.cancelMktData(t.contract)

    async def _sample(self):
        now = dt.datetime.now(dt.timezone.utc)
        batch: dict[int, list] = {}
        for key, t in list(self.tickers.items()):
            bid, ask, last = clean(t.bid), clean(t.ask), clean(t.last)
            if bid is None and ask is None:
                continue
            prev = self.last_sent.get(key)
            if prev and prev[1:] == (bid, ask) and (now - prev[0]).total_seconds() < HEARTBEAT:
                continue
            self.last_sent[key] = (now, bid, ask)
            batch.setdefault(self.want[key], []).append({"ts": now.isoformat(timespec="milliseconds"),
                                                         "bid": bid, "ask": ask, "last": last})
        for signal_id, points in batch.items():
            await self.send({"type": "quotes", "signal_id": signal_id, "points": points})

    async def run(self):
        while True:
            if not market_open():
                await asyncio.sleep(30)
                continue
            try:
                if self.ib is None or not self.ib.isConnected():
                    await self._connect()
                self._drop_expired()
                for key in [k for k in self.want if k not in self.tickers]:
                    await self._subscribe(key)
                await self._sample()
            except Exception as e:  # Gateway cerrado, reinicio diario…: se reintenta
                log(f"{type(e).__name__}: {e}; retrying")
                self.ib = None
                await asyncio.sleep(15)
                continue
            await asyncio.sleep(SAMPLE)
