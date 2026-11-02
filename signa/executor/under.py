"""VENDER AL PRECIO DEL SUBYACENTE (Trades › Trade › «Sell at SPY price»): una orden CONDICIONADA de IBKR que vende tu
opción A MERCADO cuando el subyacente (SPY, QQQ, el índice SPX…) cruza el nivel que pones. DOS por operación: la de
TP (put: al BAJAR a él; call: al SUBIR) y la de SL (al revés: put al SUBIR, call al BAJAR). Viven en IBKR (saltan
aunque el PC esté apagado) y van en el MISMO grupo OCA que tu SL: si salta una, las otras se reducen en lo vendido
(nunca se vende más de lo que tienes).

Es de Signa, no del copiador (el broker.py copiado no se toca): lleva su propio orderRef y el ejecutor la VIGILA,
porque IBKR cancela a veces la otra pata del grupo OCA cuando se sustituye el SL (el broker solo repone su SL/TP):
  · si desaparece y sigues teniendo la posición, se vuelve a poner (en el grupo del SL de ahora);
  · si vendes una parte, se ajusta a lo que te queda; si ya no tienes nada, se cancela (que nunca te deje en corto);
  · antes de cada VENTA del motor se quita, y se repone después con lo que quede.
Lo que quieres poner se guarda en %APPDATA%\\Signa\\under_targets.json (sobrevive a reinicios del ejecutor)."""
import asyncio
import json
import re
import time

from ib_async import Index, MarketOrder, PriceCondition, Stock

from signa.core.logs import HOME

REF = "signa-under"
REFS = {"tp": REF, "sl": "signa-under-sl"}  # (la de TP conserva el orderRef de siempre: las ya puestas siguen)
TARGETS = HOME / "under_targets.json"
INDICES = ("SPX", "XSP", "NDX", "RUT", "VIX")
DONE = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
AFTER_SELL = 12   # s sin tocarla tras una venta del motor (que IBKR cuente ya lo vendido)
MAX_FAILS = 5     # rechazos seguidos de IBKR al reponerla: se deja de insistir (y se dice)


def underlying(symbol: str):
    sym = symbol.upper()
    return Index(sym, "CBOE", "USD") if sym in INDICES else Stock(sym, "SMART", "USD")


class Under:
    """Las órdenes por precio del subyacente de UNA cuenta (un Under por broker)."""

    def __init__(self, account: str, broker, log=print):
        self.account, self.b, self.log = account, broker, log
        self.paused: dict[int, float] = {}  # conId -> hasta cuándo no se toca (tras una venta del motor)
        self.fails: dict[tuple, int] = {}  # (conId, «tp»|«sl») -> rechazos seguidos al reponerla
        self.errors: dict[tuple, str] = {}  # (conId, «tp»|«sl») -> el último rechazo de IBKR (lo ve la app)
        self.cons: dict[str, object] = {}  # símbolo -> contrato del subyacente (cualificado)

    # --- lo que quieres (persistido) ------------------------------------------------------------------
    def _all(self) -> dict:
        try:
            return json.loads(TARGETS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def wanted(self) -> dict[int, dict[str, dict]]:
        """{conId: {"tp": want, "sl": want}} (lo guardado por la 0.1.18-0.1.19, una sola y plana, es la de TP)."""
        out = {}
        for k, v in (self._all().get(self.account) or {}).items():
            kinds = {"tp": v} if "symbol" in v else {kd: w for kd, w in v.items() if kd in REFS and w}
            if kinds:
                out[int(k)] = kinds
        return out

    def _save(self, con_id: int, kind: str, want: dict | None):
        data = self._all()
        mine = data.setdefault(self.account, {})
        cur = mine.get(str(con_id)) or {}
        cur = {"tp": cur} if "symbol" in cur else dict(cur)
        if want is None:
            cur.pop(kind, None)
        else:
            cur[kind] = want
        if cur:
            mine[str(con_id)] = cur
        else:
            mine.pop(str(con_id), None)
        TARGETS.parent.mkdir(parents=True, exist_ok=True)
        TARGETS.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # --- IBKR ---------------------------------------------------------------------------------------
    def live(self, con_id: int, kind: str | None = None) -> list:
        """Las vivas de ese contrato: de un tipo («tp» | «sl») o de los dos."""
        refs = {REFS[kind]} if kind else set(REFS.values())
        return [t for t in self.b.ib.openTrades() if t.contract.conId == con_id and t.order.action == "SELL"
                and t.order.orderRef in refs and t.orderStatus.status not in DONE]

    def held(self, con_id: int) -> int:
        return int(sum(p.position for p in self.b.ib.positions() if p.contract.conId == con_id))

    def _oca(self, con) -> str | None:
        """El grupo OCA de tu SL / TP vivos (los del broker), para ir con ellos."""
        live = self.b._our_stops(con) + self.b._our_tps(con)
        return next((t.order.ocaGroup for t in live if t.order.ocaGroup), None)

    async def _under_con(self, symbol: str):
        if symbol not in self.cons:
            con = underlying(symbol)
            await self.b.ib.qualifyContractsAsync(con)
            if not con.conId:
                raise ValueError(f"IBKR doesn't find {symbol}")
            self.cons[symbol] = con
        return self.cons[symbol]

    async def _cancel(self, trades):
        for t in trades:
            self.b.ib.cancelOrder(t.order)
        for _ in range(40):
            if all(t.orderStatus.status in DONE for t in trades):
                return
            await asyncio.sleep(0.05)

    async def _place(self, contract, want: dict, qty: int, kind: str = "tp") -> str:
        """La orden: VENTA a mercado de `qty` cuando el subyacente cruce el nivel, en el grupo OCA del SL."""
        under = await self._under_con(want["symbol"])
        more = want["op"] == ">="
        order = MarketOrder("SELL", qty, tif=want.get("tif", "DAY"), orderRef=REFS[kind])
        order.conditions = [PriceCondition(isMore=more, price=float(want["level"]), conId=under.conId,
                                           exch=under.exchange or "SMART", triggerMethod=2)]  # 2 = último precio
        order.conditionsIgnoreRth = False  # solo en sesión regular
        order.ocaGroup = self._oca(contract) or f"copier-{contract.conId}-{int(time.time() * 1000) % 10**9}"
        order.ocaType = 2
        errs: list[str] = []

        def on_err(req_id, code, msg, _c):
            if req_id == order.orderId and code not in (399,):
                errs.append(f"[{code}] " + re.sub(r"<br>|\s+", " ", msg).strip())
        self.b.ib.errorEvent += on_err
        try:
            tr = self.b.ib.placeOrder(contract, order)
            for _ in range(60):
                await asyncio.sleep(0.05)
                if tr.orderStatus.status in DONE | {"Submitted", "PreSubmitted"}:
                    break
            await asyncio.sleep(0.2)
        finally:
            self.b.ib.errorEvent -= on_err
        what = f"#{tr.order.orderId} SELL {qty} at market when {want['symbol']} {want['op']} {want['level']:g}"
        if tr.orderStatus.status in DONE:
            return f"❌ {what} → {tr.orderStatus.status}: {errs[-1] if errs else ''}"
        return ("🎯 " if kind == "tp" else "🛡 ") + f"{what} placed"

    # --- lo que pide el motor ---------------------------------------------------------------------------
    async def set(self, plan, symbol: str, level: float, op: str, kind: str = "tp") -> str:
        """Poner (o cambiar) la de ese tipo («tp» | «sl») en ese contrato, para todo lo que tienes."""
        if kind not in REFS:
            raise ValueError(f"unknown kind {kind!r}")
        async with self.b._olock(plan):
            await self.b.ensure()
            con = plan.contract
            qty = min(int(plan.qty), self.held(con.conId)) or int(plan.qty)
            want = {"symbol": symbol.upper(), "level": float(level), "op": op, "tif": self.b._tif(plan)}
            await self._cancel(self.live(con.conId, kind))
            res = await self._place(con, want, qty, kind)
            if res.startswith("❌"):
                return res
            self._save(con.conId, kind, {**want, "qty": qty, "con": _contract_dict(con)})
            self.fails.pop((con.conId, kind), None)
            self.errors.pop((con.conId, kind), None)
            return res

    async def remove(self, plan, kind: str | None = None) -> str:
        """Quitar la de ese tipo, o las dos (kind None: lo que pedía el servidor de antes)."""
        async with self.b._olock(plan):
            await self.b.ensure()
            cid = plan.contract.conId
            n = len(self.live(cid, kind))
            await self._cancel(self.live(cid, kind))
            for kd in ([kind] if kind else list(REFS)):
                self._save(cid, kd, None)
            return f"🗑 sell at underlying price removed ({n} order{'s' if n != 1 else ''} cancelled)" if n \
                else "ℹ️ no sell-at-underlying-price order on that contract: nothing to remove"

    async def before_sell(self, con_id: int):
        """El motor va a VENDER ese contrato: fuera la nuestra (que no venda lo que ya no tendrás); keep() la repone
        con lo que quede pasados unos segundos."""
        self.paused[con_id] = time.monotonic() + AFTER_SELL
        await self._cancel(self.live(con_id))

    def snapshot(self) -> dict[str, dict]:
        """{conId: {"tp": {symbol, level, op, live, error}, "sl": {…}}} para la foto de órdenes que el ejecutor
        manda al servidor (el servidor entiende también la plana de la 0.1.18-0.1.19)."""
        return {str(cid): {kd: {"symbol": w["symbol"], "level": w["level"], "op": w["op"],
                                "live": bool(self.live(cid, kd)), "error": self.errors.get((cid, kd), "")}
                           for kd, w in kinds.items()}
                for cid, kinds in self.wanted().items()}

    async def keep(self):
        """Cada pocos segundos: que lo que quieres siga en IBKR, con la cantidad que tienes y en el grupo del SL."""
        if not self.b.ib.isConnected():
            return
        for cid, kinds in self.wanted().items():
            if time.monotonic() < self.paused.get(cid, 0):
                continue
            for kd, want in kinds.items():
                key = (cid, kd)
                have = min(self.held(cid), int(want.get("qty") or 0) or self.held(cid))  # lo de ESTA operación
                live = self.live(cid, kd)
                if have <= 0:  # ya no la tienes (SL, venta, la propia orden…): fuera, que no te deje en corto
                    await self._cancel(live)
                    self._save(cid, kd, None)
                    self.errors.pop(key, None)
                    continue
                con = _contract(want.get("con") or {})
                if con is None:
                    continue
                group = self._oca(con)
                ok = (len(live) == 1 and int(live[0].order.totalQuantity) == have
                      and (group is None or live[0].order.ocaGroup == group))
                if ok or self.fails.get(key, 0) >= MAX_FAILS:
                    continue
                await self._cancel(live)
                res = await self._place(con, want, have, kd)
                self.log(f"under {self.account} {kd} {want['symbol']} {want['op']} {want['level']:g}: "
                         f"re-placed: {res}")
                if res.startswith("❌"):
                    self.fails[key] = self.fails.get(key, 0) + 1
                    self.errors[key] = res
                else:
                    self.fails.pop(key, None)
                    self.errors.pop(key, None)


def _contract_dict(con) -> dict:
    return {"conId": con.conId, "symbol": con.symbol, "secType": con.secType, "exchange": con.exchange or "SMART",
            "currency": con.currency or "USD", "lastTradeDateOrContractMonth": con.lastTradeDateOrContractMonth,
            "strike": con.strike, "right": con.right, "multiplier": con.multiplier,
            "tradingClass": con.tradingClass}


def _contract(d: dict):
    """El contrato de la opción, para volver a poner su orden (de lo guardado: sirve tras reiniciar)."""
    if not d.get("conId"):
        return None
    from ib_async import Contract
    return Contract(**{k: v for k, v in d.items() if v not in (None, "")})
