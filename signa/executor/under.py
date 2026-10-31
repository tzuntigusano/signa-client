"""VENDER AL PRECIO DEL SUBYACENTE (Trades › Trade › «Sell at SPY price»): una orden CONDICIONADA de IBKR que vende tu
opción A MERCADO cuando el subyacente (SPY, QQQ, el índice SPX…) cruza el nivel que pones: en una put, al BAJAR a
él; en una call, al SUBIR. Vive en IBKR (salta aunque el PC esté apagado) y va en el MISMO grupo OCA que tu SL: si
salta una, la otra se reduce en lo vendido (nunca se vende más de lo que tienes).

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
        self.fails: dict[int, int] = {}
        self.errors: dict[int, str] = {}  # conId -> el último rechazo de IBKR (lo ve la app)
        self.cons: dict[str, object] = {}  # símbolo -> contrato del subyacente (cualificado)

    # --- lo que quieres (persistido) ------------------------------------------------------------------
    def _all(self) -> dict:
        try:
            return json.loads(TARGETS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def wanted(self) -> dict[int, dict]:
        return {int(k): v for k, v in (self._all().get(self.account) or {}).items()}

    def _save(self, con_id: int, want: dict | None):
        data = self._all()
        mine = data.setdefault(self.account, {})
        if want is None:
            mine.pop(str(con_id), None)
        else:
            mine[str(con_id)] = want
        TARGETS.parent.mkdir(parents=True, exist_ok=True)
        TARGETS.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # --- IBKR ---------------------------------------------------------------------------------------
    def live(self, con_id: int) -> list:
        return [t for t in self.b.ib.openTrades() if t.contract.conId == con_id and t.order.action == "SELL"
                and t.order.orderRef == REF and t.orderStatus.status not in DONE]

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

    async def _place(self, contract, want: dict, qty: int) -> str:
        """La orden: VENTA a mercado de `qty` cuando el subyacente cruce el nivel, en el grupo OCA del SL."""
        under = await self._under_con(want["symbol"])
        more = want["op"] == ">="
        order = MarketOrder("SELL", qty, tif=want.get("tif", "DAY"), orderRef=REF)
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
        return f"🎯 {what} placed"

    # --- lo que pide el motor ---------------------------------------------------------------------------
    async def set(self, plan, symbol: str, level: float, op: str) -> str:
        """Poner (o cambiar) la de ese contrato, para todo lo que tienes."""
        async with self.b._olock(plan):
            await self.b.ensure()
            con = plan.contract
            qty = min(int(plan.qty), self.held(con.conId)) or int(plan.qty)
            want = {"symbol": symbol.upper(), "level": float(level), "op": op, "tif": self.b._tif(plan)}
            await self._cancel(self.live(con.conId))
            res = await self._place(con, want, qty)
            if res.startswith("❌"):
                return res
            self._save(con.conId, {**want, "qty": qty, "con": _contract_dict(con)})
            self.fails.pop(con.conId, None)
            self.errors.pop(con.conId, None)
            return res

    async def remove(self, plan) -> str:
        async with self.b._olock(plan):
            await self.b.ensure()
            n = len(self.live(plan.contract.conId))
            await self._cancel(self.live(plan.contract.conId))
            self._save(plan.contract.conId, None)
            return f"🗑 sell at underlying price removed ({n} order{'s' if n != 1 else ''} cancelled)" if n \
                else "ℹ️ no sell-at-underlying-price order on that contract: nothing to remove"

    async def before_sell(self, con_id: int):
        """El motor va a VENDER ese contrato: fuera la nuestra (que no venda lo que ya no tendrás); keep() la repone
        con lo que quede pasados unos segundos."""
        self.paused[con_id] = time.monotonic() + AFTER_SELL
        await self._cancel(self.live(con_id))

    def snapshot(self) -> dict[str, dict]:
        """{conId: {symbol, level, op, live, error}} para la foto de órdenes que el ejecutor manda al servidor."""
        return {str(cid): {"symbol": w["symbol"], "level": w["level"], "op": w["op"],
                           "live": bool(self.live(cid)), "error": self.errors.get(cid, "")}
                for cid, w in self.wanted().items()}

    async def keep(self):
        """Cada pocos segundos: que lo que quieres siga en IBKR, con la cantidad que tienes y en el grupo del SL."""
        if not self.b.ib.isConnected():
            return
        for cid, want in self.wanted().items():
            if time.monotonic() < self.paused.get(cid, 0):
                continue
            have = min(self.held(cid), int(want.get("qty") or 0) or self.held(cid))  # lo de ESTA operación
            live = self.live(cid)
            if have <= 0:  # ya no la tienes (SL, venta, la propia orden…): fuera, que no te deje en corto
                await self._cancel(live)
                self._save(cid, None)
                self.errors.pop(cid, None)
                continue
            con = _contract(want.get("con") or {})
            if con is None:
                continue
            group = self._oca(con)
            ok = (len(live) == 1 and int(live[0].order.totalQuantity) == have
                  and (group is None or live[0].order.ocaGroup == group))
            if ok or self.fails.get(cid, 0) >= MAX_FAILS:
                continue
            await self._cancel(live)
            res = await self._place(con, want, have)
            self.log(f"under {self.account} {want['symbol']} {want['op']} {want['level']:g}: re-placed: {res}")
            if res.startswith("❌"):
                self.fails[cid] = self.fails.get(cid, 0) + 1
                self.errors[cid] = res
            else:
                self.fails.pop(cid, None)
                self.errors.pop(cid, None)


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
