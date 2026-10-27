"""Ejecucion en IBKR (conexion persistente a IB Gateway / TWS). Moose Copier.

Candado de DOS posiciones (lo elige el usuario en el asistente):
- PAPER: solo puertos de paper (4002/7497) y solo cuentas DU...
- LIVE:  solo puertos reales (4001/7496) y NUNCA cuentas DU (si no, estaria creyendo que
  opera en real y seria paper, o al reves). Cualquier discrepancia = no se opera.
- Entrada: opcion simple o SPREAD (combo BAG: compra una pata y vende la otra a la vez).
- Salida: se resuelve contra TU posicion abierta en ese ticker (WSN no repite el
  contrato al cerrar). Sin posicion que encaje, no se hace nada: nunca abre cortos.
"""

import asyncio
import time
import logging
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ib_async import (IB, ComboLeg, Contract, Index, LimitOrder, MarketOrder, Option,
                      StartupFetch, Stock, StopLimitOrder, StopOrder)

from calls import Call

log = logging.getLogger("broker")
NY = ZoneInfo("America/New_York")
PAPER_PORTS = {4002, 7497}
LIVE_PORTS = {4001, 7496}
MAX_SUBS = 20  # suscripciones de precio abiertas a la vez (tarjetas pendientes)
DONE = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "ValidationError"}


class PlanError(Exception):
    """La call no se puede convertir en una orden (se avisa, no se opera).
    suggest: vencimiento alternativo (YYYYMMDD) si el pedido no existe."""

    def __init__(self, msg: str, suggest: str | None = None):
        super().__init__(msg)
        self.suggest = suggest


class ModeError(Exception):
    """El modo elegido (paper/live) no coincide con el puerto o la cuenta: no se opera."""


@dataclass
class Plan:
    contract: Contract
    action: str          # BUY | SELL
    qty: int
    desc: str            # texto legible de lo que se va a mandar
    legs: list = field(default_factory=list)  # [(Contract, +1|-1)] para cotizar
    info: dict = field(default_factory=dict)  # salidas: held, cost, strikes, exp, right, spread


# IBKR sin permiso de datos en tiempo real para ese contrato (354, 10089, 10090, 10167, 10168, 10186, 10197):
# se pasa a datos RETRASADOS (15 min) y la tarjeta lo dice (antes salía «market closed?» con el mercado abierto)
NO_DATA_CODES = {354, 10089, 10090, 10167, 10168, 10186, 10197}


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def _bag(symbol: str, long_leg: Contract, short_leg: Contract) -> Contract:
    return Contract(secType="BAG", symbol=symbol, currency="USD", exchange="SMART", comboLegs=[
        ComboLeg(conId=long_leg.conId, ratio=1, action="BUY", exchange="SMART"),
        ComboLeg(conId=short_leg.conId, ratio=1, action="SELL", exchange="SMART")])


def _fecha(exp: str) -> str:
    return f"{exp[6:8]}/{exp[4:6]}/{exp[:4]}"


def _dia(exp: str) -> str:
    from datetime import date
    return f"{date(int(exp[:4]), int(exp[4:6]), int(exp[6:])):%a %d/%m/%Y}"


def tick_round(price: float) -> float:
    """Tick que acepta IBKR en opciones: 0.05 por debajo de 3 $, 0.10 por encima (vale para
    todas las clases, tambien las de centimo)."""
    tick = 0.05 if price < 3 else 0.10
    return max(tick, round(round(price / tick) * tick, 2))


def tpsl_price(avg: float, pct: float, kind: str) -> float:
    """kind 'tp': media +pct% · 'sl': media -pct%. Redondeado al tick."""
    return tick_round(avg * (1 + pct / 100) if kind == "tp" else avg * (1 - pct / 100))


def buy_more_plan(sell: Plan, qty: int) -> Plan:
    """Panel de una posicion: COMPRAR MAS del mismo contrato (o del mismo spread)."""
    desc = re.sub(r"^SELL \d+ of \d+", f"BUY {qty} more (you hold {sell.info.get('held', '?')})",
                  sell.desc)
    return Plan(sell.contract, "BUY", qty, desc, sell.legs, dict(sell.info))


class Broker:
    def __init__(self, host: str, port: int, client_id: int, live: bool = False):
        ports = LIVE_PORTS if live else PAPER_PORTS
        if port not in ports:
            raise ModeError(f"{'LIVE' if live else 'PAPER'} mode needs port {sorted(ports)}, "
                            f"got {port}")
        self.host, self.port, self.client_id, self.live = host, port, client_id, live
        self.ib = IB()
        self.account = ""
        self.readonly = False  # True = la API no admite ordenes (simulacion)
        self._conn_lock = asyncio.Lock()             # solo para (re)conectar
        self._locks: dict[object, asyncio.Lock] = {}  # una orden a la vez POR CONTRATO
        self._cache: dict[tuple, Contract] = {}      # (symbol, expiry, strike, right) -> contrato ya resuelto
        self._hot: dict[int, object] = {}            # conId -> Ticker siempre vivo (precio en caliente)
        self._subs: OrderedDict[int, object] = OrderedDict()  # conId -> Ticker vivo
        self._no_data: dict[int, int] = {}  # conId -> codigo de IBKR «sin permiso de datos» para ese contrato
        self._delayed = False  # se pidieron datos retrasados (con permiso, IBKR sigue dando tiempo real)
        self.ib.errorEvent += self._on_data_error

    def _olock(self, plan: Plan) -> asyncio.Lock:
        """Candado de ESE contrato: dos ordenes del mismo contrato no se cruzan (vender y su TP),
        pero dos calls distintas (Pipo y WSN a la vez) van en paralelo."""
        key = tuple(sorted(getattr(con, "conId", 0) for con, _ in plan.legs)) or id(plan)
        return self._locks.setdefault(key, asyncio.Lock())

    async def ensure(self):
        if self.ib.isConnected():
            return
        async with self._conn_lock:
            if not self.ib.isConnected():  # otro ya conecto mientras esperabamos
                await self._connect()

    async def _connect(self):
        await self.ib.connectAsync(self.host, self.port, clientId=self.client_id, timeout=15,
                                   fetchFields=StartupFetch.POSITIONS, readonly=self.readonly)
        accts = self.ib.managedAccounts()
        paper = bool(accts) and all(a.startswith("DU") for a in accts)
        if not accts or paper == self.live:
            self.ib.disconnect()
            want = "a LIVE account (U…)" if self.live else "a PAPER account (DU…)"
            raise ModeError(f"IB Gateway is logged into {accts or 'no account'}, but this app "
                            f"is set to {'LIVE' if self.live else 'PAPER'} mode and needs {want}")
        self.account = accts[0]
        self._subs.clear()  # conexion nueva: las suscripciones anteriores murieron
        self._hot.clear()
        self._no_data.clear()
        self.ib.reqMarketDataType(3 if self._delayed else 1)
        log.info("IB connected, %s account %s", "LIVE" if self.live else "paper", self.account)

    # ------------------------------------------------------------ planes
    async def plan(self, c: Call, suggest: bool = True) -> Plan:
        """suggest=False: si el contrato no existe, falla YA (sin buscar en la cadena el siguiente
        vencimiento para sugerirlo, que cuesta 1-3 s)."""
        await self.ensure()
        return await (self._plan_entry(c, suggest) if c.kind == "entry" else self._plan_exit(c))

    async def next_day_plan(self, c: Call, days: int = 5) -> Plan | None:
        """RAPIDO: la 0DTE no existe -> prueba directamente los siguientes dias habiles (manana,
        pasado...) sin pedir la cadena entera; cada intento fallido cuesta ~0,1 s. Si encuentra uno,
        deja c.expiry en ese vencimiento y devuelve su plan; si no, None (sin tocar c)."""
        import copy
        from datetime import timedelta
        d = datetime.strptime(c.expiry, "%Y%m%d")
        for _ in range(days):
            d += timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            alt = copy.deepcopy(c)
            alt.expiry = d.strftime("%Y%m%d")
            try:
                plan = await self._plan_entry(alt, suggest=False)
            except PlanError:
                continue
            c.expiry = alt.expiry
            return plan
        return None

    async def next_expiry(self, c: Call) -> str | None:
        """Siguiente vencimiento (posterior al pedido) que tenga TODOS los strikes de la call."""
        try:
            if c.symbol == "SPX":
                und, sec, tclass = Index("SPX", "CBOE", "USD"), "IND", "SPXW"
            else:
                und, sec, tclass = Stock(c.symbol, "SMART", "USD"), "STK", c.symbol
            await self.ib.qualifyContractsAsync(und)
            chains = await self.ib.reqSecDefOptParamsAsync(c.symbol, "", sec, und.conId)
            strikes = {lg.strike for lg in c.legs}
            exps = sorted({e for ch in chains if ch.tradingClass == tclass
                           and strikes <= set(ch.strikes) for e in ch.expirations})
            return next((e for e in exps if e > c.expiry), None)
        except Exception as e:
            log.warning("next_expiry %s: %s", c.symbol, e)
            return None

    async def expiry_by_price(self, c: Call, price: float, tries: int = 4):
        """La cotizacion no se parece a su precio (¿otro vencimiento?): prueba los siguientes
        vencimientos con esos strikes y devuelve (vencimiento, bid, ask) del que tenga el ask mas
        parecido a `price`, o None si ninguno encaja (dentro de ±35 %)."""
        import copy
        try:
            if c.symbol == "SPX":
                und, sec, tclass = Index("SPX", "CBOE", "USD"), "IND", "SPXW"
            else:
                und, sec, tclass = Stock(c.symbol, "SMART", "USD"), "STK", c.symbol
            await self.ib.qualifyContractsAsync(und)
            chains = await self.ib.reqSecDefOptParamsAsync(c.symbol, "", sec, und.conId)
            strikes = {lg.strike for lg in c.legs}
            exps = sorted({e for ch in chains if ch.tradingClass == tclass
                           and strikes <= set(ch.strikes) for e in ch.expirations})
        except Exception as e:
            log.warning("expiry_by_price %s: %s", c.symbol, e)
            return None
        best = None
        for exp in [e for e in exps if e != c.expiry and e >= datetime.now(NY).strftime("%Y%m%d")][:tries]:
            alt = copy.deepcopy(c)
            alt.expiry = exp
            try:
                plan = await self._plan_entry(alt, suggest=False)
                bid, ask = await self.quote(plan)
            except Exception:
                continue
            if ask:
                gap = abs(ask / price - 1)
                if best is None or gap < best[0]:
                    best = (gap, exp, bid, ask, plan)
                else:
                    self.release(plan)
        if best and best[0] <= 0.35:
            return best[1], best[2], best[3]
        return None

    async def _plan_entry(self, c: Call, suggest: bool = True) -> Plan:
        # SPX: las diarias (0DTE) son de la clase SPXW; SPXW cubre todos los vencimientos
        tclass = "SPXW" if c.symbol == "SPX" else ""
        keys = [(c.symbol, c.expiry, float(lg.strike), lg.right[:1].upper()) for lg in c.legs]
        if all(k in self._cache for k in keys):  # precalentado: sin preguntar a IBKR
            q = [self._cache[k] for k in keys]
        else:
            opts = [Option(c.symbol, c.expiry, lg.strike, lg.right, "SMART", currency="USD",
                           tradingClass=tclass) for lg in c.legs]
            q = await self.ib.qualifyContractsAsync(*opts)
            for k, x in zip(keys, q):
                if x and x.conId:
                    self._cache[k] = x
        if len(q) != len(c.legs) or not all(x and x.conId for x in q):
            nxt = await self.next_expiry(c) if suggest else None
            d = c.expiry
            if nxt:
                raise PlanError(f"No {c.symbol} option expires {_dia(d)} with strike "
                                f"{'/'.join(f'{lg.strike:g}' for lg in c.legs)} (not every stock "
                                f"has 0DTE every day). Next expiry: {_dia(nxt)}", suggest=nxt)
            raise PlanError(f"IBKR can't find the contract ({c.describe()})")
        if len(q) == 1:
            return Plan(q[0], "BUY", c.qty, c.describe(False), [(q[0], 1)])
        return Plan(_bag(c.symbol, q[0], q[1]), "BUY", c.qty, c.describe(False),
                    [(q[0], 1), (q[1], -1)])

    async def prewarm(self, symbols, expiry: str) -> int:
        """Resuelve de golpe TODOS los strikes de ese vencimiento (calls y puts) de esos simbolos:
        una peticion por simbolo. Luego _plan_entry no pregunta a IBKR (ahorra 0,1-0,3 s)."""
        await self.ensure()
        n = 0
        for sym in symbols:
            tclass = "SPXW" if sym == "SPX" else ""
            try:
                cds = await self.ib.reqContractDetailsAsync(
                    Option(sym, expiry, 0, "", "SMART", currency="USD", tradingClass=tclass))
            except Exception as e:
                log.warning("prewarm %s %s: %s", sym, expiry, e)
                continue
            for cd in cds:
                con = cd.contract
                con.exchange = "SMART"
                self._cache[(sym, expiry, float(con.strike), con.right[:1].upper())] = con
                n += 1
        return n

    def _structures(self, symbol: str):
        """Agrupa tus opciones de un ticker en operaciones: simples o spreads."""
        pos = [p for p in self.ib.positions()
               if p.contract.secType == "OPT" and p.contract.symbol == symbol and p.position]
        groups: dict[tuple, list] = {}
        for p in pos:
            k = (p.contract.lastTradeDateOrContractMonth, p.contract.right)
            groups.setdefault(k, []).append(p)
        out = []
        for (exp, right), ps in groups.items():
            longs = [p for p in ps if p.position > 0]
            shorts = [p for p in ps if p.position < 0]
            for lp in longs:
                sp = next((s for s in shorts if abs(s.position) == lp.position), None)
                if sp:
                    shorts.remove(sp)
                    out.append({"kind": "spread", "exp": exp, "right": right, "qty": int(lp.position),
                                "long": lp.contract, "short": sp.contract,
                                "strikes": {lp.contract.strike, sp.contract.strike},
                                "cost": (lp.avgCost - abs(sp.avgCost)) / 100})  # debito neto
                else:
                    out.append({"kind": "single", "exp": exp, "right": right, "qty": int(lp.position),
                                "long": lp.contract, "strikes": {lp.contract.strike},
                                "cost": lp.avgCost / 100})  # avgCost de IB = por contrato
        return out

    async def _plan_exit(self, c: Call) -> Plan:
        structs, symbol = [], ""
        for cand in c.candidates:
            structs = self._structures(cand)
            if structs:
                symbol = cand
                break
        if not structs:
            raise PlanError(f"you have no open options in {c.symbol or '?'}: nothing to close")
        hints = {lg.strike for lg in c.legs}
        fit = [s for s in structs
               if (not c.right or s["right"] == c.right)
               and (not c.expiry or s["exp"] == c.expiry)
               and (not c.spread or s["kind"] == "spread")
               and hints <= s["strikes"]]
        typo = ""
        if not fit and hints:  # errata en el strike («SKHY 154c» por 145c): si solo hay UNA, esa
            same = [s for s in structs if not c.right or s["right"] == c.right]
            if len(same) == 1:
                fit = same
                typo = (f"strike {'/'.join(f'{h:g}' for h in sorted(hints))} not in your positions: "
                        f"using your only {symbol} {sorted(same[0]['strikes'])[0]:g}{same[0]['right']}")
        if len(fit) != 1:
            lista = "; ".join(f"{s['kind']} {sorted(s['strikes'])}{s['right']} {_fecha(s['exp'])} x{s['qty']}"
                              for s in structs)
            raise PlanError(("none" if not fit else "several") +
                            f" of your {symbol} positions match ({lista}): close it manually")
        s = fit[0]
        held = s["qty"]
        if c.keep is not None:  # "dejo 1 runner": todo menos esos
            qty = held - c.keep
            if qty <= 0:
                raise PlanError(f"you hold {held} and he keeps {c.keep} as runner: nothing to sell")
        elif c.frac is not None:  # proporcional a lo que cierra el ("8/10" = 80%), minimo 1
            qty = min(held, max(1, round(held * c.frac)))
        else:
            qty = held if c.qty is None else min(c.qty, held)
        info = {"held": held, "cost": s["cost"], "exp": s["exp"], "right": s["right"],
                "spread": s["kind"] == "spread", "strike_list": sorted(s["strikes"]),
                "note": typo}
        long_c = Contract(conId=s["long"].conId, exchange="SMART")
        if s["kind"] == "single":
            await self.ib.qualifyContractsAsync(long_c)
            long_c.exchange = "SMART"
            desc = f"SELL {qty} of {s['qty']} · {symbol} {s['long'].strike:g}{s['right']} {_fecha(s['exp'])}"
            info["strikes"] = f"{s['long'].strike:g}"
            return Plan(long_c, "SELL", qty, desc, [(long_c, 1)], info)
        short_c = Contract(conId=s["short"].conId, exchange="SMART")
        await self.ib.qualifyContractsAsync(long_c, short_c)
        long_c.exchange = short_c.exchange = "SMART"
        desc = (f"SELL {qty} of {s['qty']} · {symbol} spread +{s['long'].strike:g}/"
                f"−{s['short'].strike:g}{s['right']} {_fecha(s['exp'])}")
        info["strikes"] = f"+{s['long'].strike:g} / −{s['short'].strike:g}"
        return Plan(_bag(symbol, long_c, short_c), "SELL", qty, desc,
                    [(long_c, 1), (short_c, -1)], info)

    # ------------------------------------------------------------ precios
    def _ticker(self, con):
        """Suscripcion VIVA por contrato (se mantiene mientras la tarjeta esta abierta, asi
        Refresh lee el precio actual al momento). Tope MAX_SUBS: la paper tiene lineas limitadas."""
        if con.conId in self._hot:  # precio en caliente: ya esta al dia, sin esperar
            return self._hot[con.conId], False
        if con.conId in self._subs:
            self._subs.move_to_end(con.conId)
            return self._subs[con.conId], False
        while len(self._subs) >= MAX_SUBS:
            _, old = self._subs.popitem(last=False)
            self.ib.cancelMktData(old.contract)
        t = self.ib.reqMktData(con, "", False, False)
        # ib_async devuelve el MISMO Ticker de una suscripcion anterior con su bid/ask VIEJO, y el primer dato
        # que llega puede no ser de precio: quote() lo daba por bueno (21/10 SPX 7765C: 4,10 de 5 min antes con
        # el bid real en 5,50). Se borran para esperar uno de verdad.
        t.bid = t.ask = float("nan")
        self._subs[con.conId] = t
        return t, True

    async def keep_hot(self, symbols, expiry: str, width: int = 3) -> int:
        """PRECIO EN CALIENTE: deja suscritos los `width` strikes de cada lado del precio (calls y
        puts, 0DTE ya precalentado) para que el filtro de 'no perseguir' tenga el ask AL MOMENTO.
        Llamar cada pocos minutos: se recentra si el indice se mueve. Devuelve las suscripciones."""
        await self.ensure()
        want: dict[int, Contract] = {}
        for sym in symbols:
            und = Index("SPX", "CBOE", "USD") if sym == "SPX" else Stock(sym, "SMART", "USD")
            try:
                await self.ib.qualifyContractsAsync(und)
                [t] = await self.ib.reqTickersAsync(und)
                px = _num(t.marketPrice()) or _num(t.close)
            except Exception as e:
                log.warning("keep_hot %s: %s", sym, e)
                continue
            strikes = sorted({k[2] for k in self._cache if k[0] == sym and k[1] == expiry})
            if not px or not strikes:
                continue
            near = sorted(strikes, key=lambda k: abs(k - px))[:2 * width + 1]
            for k in near:
                for r in ("C", "P"):
                    if con := self._cache.get((sym, expiry, k, r)):
                        want[con.conId] = con
        for cid in [c for c in self._hot if c not in want]:  # el indice se movio: fuera los lejanos
            t = self._hot.pop(cid)
            if cid not in self._subs:
                self.ib.cancelMktData(t.contract)
        for cid, con in want.items():
            if cid not in self._hot:
                self._hot[cid] = self._subs.pop(cid, None) or self.ib.reqMktData(con, "", False, False)
        return len(self._hot)

    def release(self, plan: Plan):
        """Tarjeta cerrada: fuera sus suscripciones."""
        for con, _ in plan.legs:
            if getattr(con, "conId", None) in self._hot:
                continue  # el precio en caliente se queda
            if t := self._subs.pop(getattr(con, "conId", None), None):
                self.ib.cancelMktData(t.contract)

    def _on_data_error(self, req_id, code, msg, contract):
        """Sin permiso de datos en tiempo real: datos retrasados para ese contrato (y los siguientes)."""
        cid = getattr(contract, "conId", 0)
        if code not in NO_DATA_CODES or not cid:
            return
        self._no_data[cid] = code
        if not self._delayed:
            self._delayed = True
            log.warning("IBKR: no real-time data permission (%s: %s): using DELAYED data", code, msg)
            self.ib.reqMarketDataType(3)
        for subs in (self._subs, self._hot):
            if (t := subs.get(cid)) is not None:  # se pide otra vez, ya en retrasado
                self.ib.cancelMktData(t.contract)
                subs[cid] = self.ib.reqMktData(t.contract, "", False, False)

    def _note(self, plan: Plan):
        """plan.info['quote_note'] para la tarjeta: 'delayed' (precio de hace 15 min) o 'no_permission'."""
        note = ""
        for con, _ in plan.legs:
            cid = getattr(con, "conId", 0)
            t = self._hot.get(cid) or self._subs.get(cid)
            if t is not None and getattr(t, "marketDataType", 1) in (3, 4):
                note = "delayed"
            elif cid in self._no_data and not note:
                note = "no_permission"
        plan.info["quote_note"] = note

    def peek(self, plan: Plan) -> tuple[float | None, float | None]:
        """bid/ask AL INSTANTE de las suscripciones vivas (sin esperar). Para refrescar la
        tarjeta cada segundo; la primera cotizacion la da quote(), que si espera un dato nuevo."""
        if not self.ib.isConnected() or not plan.legs:
            self._note(plan)
            return None, None
        bid = ask = 0.0
        for con, sign in plan.legs:
            t, _ = self._ticker(con)
            b, a = _num(t.bid), _num(t.ask)
            if b is None or a is None or b < 0 or a <= 0:
                self._note(plan)
                return None, None
            bid += b if sign > 0 else -a
            ask += a if sign > 0 else -b
        self._note(plan)
        return round(bid, 2), round(ask, 2)

    async def quote(self, plan: Plan) -> tuple[float | None, float | None]:
        """bid/ask de la operacion (en spreads, del combo: pata larga − pata corta)."""
        await self.ensure()
        legs = [(*self._ticker(con), sign) for con, sign in plan.legs]
        # OJO: ib_async reutiliza el Ticker de peticiones anteriores con el bid/ask VIEJO.
        # En una suscripcion nueva hay que esperar a un dato posterior a la peticion.
        start = {id(t): t.time for t, new, _ in legs if new}
        fresh = False
        for _ in range(60):  # hasta 3 s
            fresh = all(t.time != start[id(t)] for t, new, _ in legs if new)
            if fresh and all(_num(t.bid) is not None and _num(t.ask) is not None
                             for t, _, _ in legs):
                break
            await asyncio.sleep(0.05)
        if not fresh:  # no ha llegado ningun dato nuevo: mejor "sin precio" que uno viejo
            self._note(plan)
            return None, None
        bid = ask = 0.0
        for t, _, sign in legs:
            b, a = _num(t.bid), _num(t.ask)
            if b is None or a is None or b < 0 or a <= 0:  # IBKR da -1 sin cotizacion
                self._note(plan)
                return None, None
            bid += b if sign > 0 else -a
            ask += a if sign > 0 else -b
        self._note(plan)
        return round(bid, 2), round(ask, 2)

    # ------------------------------------------------------------ ejecucion
    # ------------------------------------------------------------ stops / take profit
    # TP y SL de un mismo contrato van en un grupo OCA (tipo 2): si uno se ejecuta, los demas
    # se REDUCEN en lo vendido -> nunca se vende mas de lo que tienes.
    TP_REF = "copier-tp"

    def _our_stops(self, con: Contract):
        """Stops de VENTA que ESTE programa tiene vivos sobre ese contrato."""
        return [t for t in self.ib.openTrades()
                if t.contract.conId == con.conId and t.order.action == "SELL"
                and t.order.orderType in ("STP", "STP LMT")
                and t.orderStatus.status not in DONE]

    def _our_tps(self, con: Contract):
        """Take profits (LMT de venta marcados TP_REF) de ESTE programa sobre ese contrato."""
        return [t for t in self.ib.openTrades()
                if t.contract.conId == con.conId and t.order.action == "SELL"
                and t.order.orderRef == self.TP_REF and t.orderStatus.status not in DONE]

    async def _cancel(self, trades) -> int:
        for t in trades:
            self.ib.cancelOrder(t.order)
        for _ in range(40):
            if all(t.orderStatus.status in DONE for t in trades):
                break
            await asyncio.sleep(0.05)
        return len(trades)

    async def cancel_stops(self, con: Contract, tps: bool = True) -> int:
        """Cancela nuestros stops (y TPs) de ese contrato antes de vender: una orden de venta
        huerfana tras vender podria dejarte en corto."""
        return await self._cancel(self._our_stops(con) + (self._our_tps(con) if tps else []))

    async def _protect(self, plan: Plan, order, what: str, replaced: int, icon: str,
                       on_fill=None) -> str:
        """Envia un TP/SL de VENTA (grupo OCA del contrato) y espera la confirmacion."""
        # grupo OCA: el de las ordenes VIVAS de esta posicion o uno NUEVO. Antes era siempre
        # "copier-<conId>" y, si ese grupo ya habia tenido una ejecucion (otro bot, otro dia), IBKR
        # rechazaba todo con "OCA group is already filled" (19/10/2026: QQQ 725C se quedo sin SL)
        live = self._our_stops(plan.contract) + self._our_tps(plan.contract)
        group = next((t.order.ocaGroup for t in live if t.order.ocaGroup), None) or \
            f"copier-{plan.contract.conId}-{int(time.time() * 1000) % 10**9}"
        order.ocaGroup, order.ocaType = group, 2
        errs: list[str] = []

        def on_err(req_id, code, msg, _c):
            if req_id == order.orderId and code not in (399,):
                errs.append(f"[{code}] " + re.sub(r"<br>|\s+", " ", msg).strip())
        self.ib.errorEvent += on_err
        try:
            tr = self.ib.placeOrder(plan.contract, order)
            for _ in range(60):
                await asyncio.sleep(0.05)
                if tr.orderStatus.status in DONE | {"Submitted", "PreSubmitted"}:
                    break
            await asyncio.sleep(0.2)
        finally:
            self.ib.errorEvent -= on_err
        what = f"#{tr.order.orderId} {what}"
        if tr.orderStatus.status in DONE:
            try:
                self.ib.cancelOrder(tr.order)
            except Exception:
                pass
            return f"❌ {what} → {tr.orderStatus.status}: {errs[-1] if errs else ''}"
        if on_fill:  # TP/SL ALCANZADO (se ejecuta mas tarde)
            tr.filledEvent += lambda t: on_fill(t.orderStatus.avgFillPrice, int(t.orderStatus.filled))
        return f"{icon} {what} placed" + (f" · replaced {replaced} previous" if replaced else "")

    def _tif(self, plan: Plan) -> str:
        today = datetime.now(NY).strftime("%Y%m%d")
        return "DAY" if plan.info.get("exp", "") <= today else "GTC"  # 0DTE: del dia

    async def _restore(self, plan: Plan, partners) -> str:
        """IBKR CANCELA la otra pata del grupo OCA cuando sustituimos o quitamos una (visto 19/10/2026:
        al subir el stop a break-even se cayo el TP de META y NVDA). Si la pareja quedo cancelada (no
        ejecutada), se vuelve a poner igual, dentro del mismo grupo OCA."""
        for _ in range(20):
            await asyncio.sleep(0.05)
        out = []
        for t in partners:
            if t.orderStatus.status not in ("Cancelled", "ApiCancelled", "Inactive"):
                continue
            o = t.order
            if o.orderType == "LMT":
                new = LimitOrder("SELL", o.totalQuantity, o.lmtPrice, tif=o.tif, orderRef=self.TP_REF)
                what, icon = f"TAKE PROFIT SELL {o.totalQuantity:g} at {o.lmtPrice:.2f} ({o.tif})", "🎯"
            elif o.orderType == "STP LMT":
                new = StopLimitOrder("SELL", o.totalQuantity, o.lmtPrice, o.auxPrice, tif=o.tif)
                what, icon = f"STOP-LIMIT SELL {o.totalQuantity:g} at {o.auxPrice:.2f} ({o.tif})", "🛑"
            else:
                new = StopOrder("SELL", o.totalQuantity, o.auxPrice, tif=o.tif)
                what, icon = f"STOP (market) SELL {o.totalQuantity:g} at {o.auxPrice:.2f} ({o.tif})", "🛑"
            r = await self._protect(plan, new, what, 0, icon)
            log.info("OCA partner restored: %s", r)
            out.append(("re-placed " if not r.startswith("❌") else "") + r)
        return (" · " + " · ".join(out)) if out else ""

    async def set_stop(self, plan: Plan, stop: float, stop_limit: bool, on_fill=None) -> str:
        """Stop de VENTA (STP = a mercado al tocar; STP LMT = limite = stop) sobre tu posicion.
        Reemplaza el stop anterior que hubiera puesto este programa en ese contrato."""
        async with self._olock(plan):
            await self.ensure()
            if plan.info.get("spread"):
                return "❌ stops on spreads aren't supported here: set it in IBKR"
            partners = self._our_tps(plan.contract)  # su TP (misma OCA): IBKR puede cancelarlo
            replaced = await self._cancel(self._our_stops(plan.contract))
            tif, stop = self._tif(plan), round(stop, 2)
            order = (StopLimitOrder("SELL", plan.qty, stop, stop, tif=tif) if stop_limit
                     else StopOrder("SELL", plan.qty, stop, tif=tif))
            kind = "STOP-LIMIT" if stop_limit else "STOP (market)"
            res = await self._protect(plan, order, f"{kind} SELL {plan.qty} at {stop:.2f} ({tif})",
                                      replaced, "🛑", on_fill)
            return res + await self._restore(plan, partners)

    async def remove_protect(self, plan: Plan, kind: str) -> str:
        """Quita nuestro TP (kind 'tp') o nuestros stops (kind 'sl') de ese contrato."""
        async with self._olock(plan):
            await self.ensure()
            tp = kind == "tp"
            n = await self._cancel(self._our_tps(plan.contract) if tp else self._our_stops(plan.contract))
            name = "take profit" if tp else "stop loss"
            return f"🗑 {name} removed ({n} order{'s' if n != 1 else ''} cancelled)" if n else \
                f"ℹ️ no {name} of this copier on that contract: nothing to remove"

    async def set_tp(self, plan: Plan, price: float, on_fill=None) -> str:
        """Take profit: LIMITE de VENTA a ese precio. Reemplaza el TP anterior de ese contrato."""
        async with self._olock(plan):
            await self.ensure()
            if plan.info.get("spread"):
                return "❌ take profits on spreads aren't supported here: set it in IBKR"
            partners = self._our_stops(plan.contract)  # su SL (misma OCA): IBKR puede cancelarlo
            replaced = await self._cancel(self._our_tps(plan.contract))
            tif, price = self._tif(plan), round(price, 2)
            order = LimitOrder("SELL", plan.qty, price, tif=tif, orderRef=self.TP_REF)
            res = await self._protect(plan, order, f"TAKE PROFIT SELL {plan.qty} at {price:.2f} ({tif})",
                                      replaced, "🎯", on_fill)
            return res + await self._restore(plan, partners)

    async def execute(self, plan: Plan, limit: float | None, on_fill=None) -> str:
        async with self._olock(plan):
            await self.ensure()
            if plan.action == "SELL" and plan.contract is not None:
                await self.cancel_stops(plan.contract)  # vendes tu: fuera tus stops de ese contrato
            order = (MarketOrder(plan.action, plan.qty, tif="DAY") if limit is None
                     else LimitOrder(plan.action, plan.qty, round(limit, 2), tif="DAY"))
            errs: list[str] = []  # rechazos de IBKR (p. ej. 201 "sin efectivo"): el motivo REAL

            def on_err(req_id, code, msg, _c):
                if req_id == order.orderId and code not in (399,):  # 399 = aviso informativo
                    errs.append(f"[{code}] " + re.sub(r"<br>|\s+", " ", msg).strip())
            self.ib.errorEvent += on_err
            try:
                tr = self.ib.placeOrder(plan.contract, order)
                for _ in range(100):  # hasta 5 s
                    await asyncio.sleep(0.05)
                    if tr.orderStatus.status in DONE:
                        break
                await asyncio.sleep(0.2)  # el texto del error llega justo despues del estado
            finally:
                self.ib.errorEvent -= on_err
            s = tr.orderStatus
            head = f"#{tr.order.orderId} {plan.desc} · " + ("MKT" if limit is None else f"LMT {limit:.2f}")
            if s.status == "Filled":
                return f"✅ {head} → filled at {s.avgFillPrice:.2f}"
            if s.status in DONE:
                why = errs[-1] if errs else next((e.message for e in reversed(tr.log)
                                                  if e.message), s.status)
                # OJO (15/10/2026): una orden "rechazada" por Read-Only API (aviso 321) se quedo
                # en Gateway y se ENVIO 8 min despues al desmarcarlo. Cancelarla siempre de forma
                # explicita para que un rechazo sea definitivo.
                try:
                    self.ib.cancelOrder(tr.order)
                except Exception:
                    pass
                return f"❌ {head} → {s.status}: {why}"
            if on_fill:
                tr.filledEvent += lambda t: on_fill(
                    f"✅ {head} → filled at {t.orderStatus.avgFillPrice:.2f}")
            return f"⏳ {head} → {s.status} (working; valid today)"

    async def summary(self) -> str:
        await self.ensure()
        lines = [f"{'🔴 LIVE' if self.live else 'Paper'} account {self.account}"]
        opts = [p for p in self.ib.positions() if p.contract.secType == "OPT"]
        for p in opts:
            lines.append(f"• {p.contract.localSymbol}  x{p.position:g}  cost {p.avgCost / 100:.2f}")
        if not opts:
            lines.append("No open options.")
        return "\n".join(lines)

    async def realized_today(self, day) -> dict:
        """P&L REALIZADO del dia (hora NY) a partir de las ejecuciones de IBKR: cada cierre
        trae su realizedPNL ya calculado (con comisiones). Agrupado por contrato."""
        await self.ensure()
        fills = await self.ib.reqExecutionsAsync()
        await asyncio.sleep(0.5)  # los commissionReport llegan un poco despues
        by: dict[str, dict] = {}
        for f in self.ib.fills():
            if f.time.astimezone(NY).date() != day:
                continue
            rp = f.commissionReport.realizedPNL if f.commissionReport else 0.0
            if not rp or abs(rp) > 1e300:  # 1.79e308 = sin valor (aperturas)
                continue
            mult = float(f.contract.multiplier or 1)
            proceeds = f.execution.shares * f.execution.price * mult
            k = f.contract.localSymbol or f.contract.symbol
            d = by.setdefault(k, {"pnl": 0.0, "cost": 0.0, "qty": 0.0})
            d["pnl"] += rp
            d["qty"] += f.execution.shares
            # coste de lo cerrado: lo cobrado - lo ganado (largos) / lo pagado + lo ganado (cortos)
            d["cost"] += proceeds - rp if f.execution.side == "SLD" else proceeds + rp
        netliq = next((float(v.value) for v in await self.ib.accountSummaryAsync(self.account)
                       if v.tag == "NetLiquidation" and v.currency == "USD"), None)
        return {"by": by, "netliq": netliq, "n_fills": len(fills)}
