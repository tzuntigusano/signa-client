"""La call ya interpretada (Call/Leg, lo que sale de cualquier parser de proveedor), utilidades de fechas y el
intérprete genérico de calls en inglés (parse). Copia del copiador original: los parsers de proveedores
dependen de esto.

Intérprete genérico DETERMINISTA de calls de opciones (texto -> orden).

Reglas acordadas con el usuario:
- Entradas: N contratos por defecto (WSN = 2) salvo que la call diga otra cantidad.
- Salidas: un cierre PARCIAL (1/2, 1/4, another, partial, trim) vende 1 contrato;
  un cierre TOTAL (remainder, balance, closed, stopped...) vende todo lo que quede.
  La salida no suele repetir el contrato: se resuelve contra TU posicion abierta.
- Spreads ("300 / 310 CALL SPREAD"): se copian como combo. LONG = de debito:
  en calls se compra el strike bajo; en puts, el alto.
- Sin fecha = 0DTE; "1DTE" = siguiente dia habil; solo mes = mensual (3er viernes).
- Precio con decimales = prima tal cual; sin decimales = centimos ("90" = 0.90);
  un entero de una cifra es ambiguo -> error. "market"/sin precio = a mercado.
- Nunca abre cortos. Opciones sobre futuros y acciones: no se copian.
Nada de LLM: la misma call da siempre la misma orden. Si no encaja, no se opera.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
MONTHS.update({"SEPT": 9, "ENE": 1, "ABR": 4, "AGO": 8, "DIC": 12})
MONTHS.update({m: i for i, m in enumerate(  # nombres completos («MARCH 15 CALLS» = mensual de marzo)
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
     "OCTOBER", "NOVEMBER", "DECEMBER"], 1)})
MONTHS.update({m: i for i, m in enumerate(
    ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE",
     "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"], 1)})
FUTURES = {"MNQ", "NQ", "ES", "MES", "RTY", "M2K", "YM", "MYM", "CL", "MCL", "GC", "MGC",
           "SI", "ZB", "ZN", "ZF", "NG", "HG", "BTC", "MBT"}

ENTRY_WORDS = {"LONG", "BUY", "BTO", "COMPRA", "COMPRAR"}
EXIT_WORDS = {"CLOSING", "CLOSED", "CLOSE", "SELL", "SELLING", "SOLD", "STC", "STOPPED",
              "EXIT", "EXITING", "OUT", "TRIM", "TRIMMING", "CERRAR", "CIERRO", "VENDO",
              "VENDER"}
PARTIAL_WORDS = {"PARTIAL", "HALF", "ANOTHER", "SLICE", "TRIM", "TRIMMING", "SOME", "MITAD"}
FULL_WORDS = {"REMAINDER", "BALANCE", "REST", "ALL", "FULL", "STOPPED", "ENTIRE"}
NOTICE_WORDS = {"CANCEL", "CANCELLED", "CANCELED", "CORRECTION", "DISREGARD", "IGNORE",
                "MISTAKE", "TYPO"}
MARKET_WORDS = {"MARKET", "MKT", "MERCADO"}
RIGHT_WORDS = {"CALL": "C", "CALLS": "C", "PUT": "P", "PUTS": "P"}
STOP = (ENTRY_WORDS | EXIT_WORDS | PARTIAL_WORDS | FULL_WORDS | NOTICE_WORDS | MARKET_WORDS
        | set(RIGHT_WORDS) | set(MONTHS) | {
            "SHORT", "STO", "SPREAD", "SPREADS", "LEG", "OF", "THE", "AND", "OR", "IN", "ON",
            "AT", "A", "AN", "TO", "FOR", "MY", "OUR", "HERE", "NOW", "SIMILAR", "LEVELS",
            "LEVEL", "AROUND", "PORT", "DTE", "ODTE", "CON", "C", "P", "ETF", "NFA",
            "LOTTO", "SWING", "DAY", "TRADE", "SIZE", "PRICE", "TP", "SL", "STOP", "TARGET",
            "CONTRATO", "CONTRATOS", "CONTRACT", "CONTRACTS", "DE", "LA", "EL", "Y", "EN"})

PCT_RE = re.compile(r"[+-]?\s*\d+(?:\.\d+)?\s*%")
PRICE_RES = [re.compile(r"@\s*\$?(\d*\.?\d+)"),
             re.compile(r"\bAT\s+\$?(\d*\.\d+|\d+)\b"),
             re.compile(r"\$?(\d*\.?\d+)\s*/\s*CON\b"),
             re.compile(r"\bAROUND\s+\$?(\d*\.\d+)")]
FRACTION_RE = re.compile(r"\b([1-3])\s*/\s*([2-4])\b")
PAIR_RE = re.compile(r"\$?(\d+(?:\.\d+)?)\s*/\s*\$?(\d+(?:\.\d+)?)")
STRIKE_RE = re.compile(r"\$?(\d+(?:\.\d+)?)\s*(CALLS?|PUTS?|C|P)\b")
DTE_RE = re.compile(r"\b(\d)\s*DTE\b")
SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
MONTH_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b"
                      r"\s*(?:\(\s*(\d{1,2})\s*\)|(20\d\d)\b|(\d{1,2})\b)?")
QTY_RE = re.compile(r"\b(\d+)\s*(?:CONTRATOS?|CONTRACTS?|CTS?)\b")
TICKER_RE = re.compile(r"\$?\b([A-Z]{1,5})\b")


@dataclass
class Leg:
    strike: float
    right: str   # C | P
    action: str  # BUY | SELL (al abrir)


@dataclass
class Call:
    raw: str
    kind: str = ""              # entry | exit | stock | notice
    symbol: str = ""
    candidates: list[str] = field(default_factory=list)  # tickers posibles (salidas)
    expiry: str = ""            # YYYYMMDD ("" en salidas sin fecha)
    legs: list[Leg] = field(default_factory=list)        # entrada: patas; salida: pistas
    right: str = ""             # pista C/P (salidas)
    spread: bool = False
    qty: int | None = None      # None en salida total = todo lo que quede
    fraction: str = ""          # salida: partial | full
    frac: float | None = None   # salida proporcional: vender esta fraccion (8/10 -> 0.8)
    keep: int | None = None     # salida: vender todo menos estos (runners)
    limit: float | None = None  # None = a mercado (en kind="stop": precio del stop)
    stop_limit: bool = False    # kind="stop": STP LMT (si no, STP = a mercado al tocar)
    trail: int = 0              # kind="stop": trailing al minimo de la vela anterior de N min
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def contract(self) -> tuple | None:
        """(símbolo, strike, C/P, vencimiento) de la primera pata: la «última call» de un proveedor."""
        if not self.legs:
            return None
        return self.symbol, self.legs[0].strike, self.legs[0].right or self.right, self.expiry

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self, with_price: bool = True) -> str:
        d = self.expiry
        fecha = f"{d[6:8]}/{d[4:6]}/{d[:4]}" if d else ""
        px = ("MKT" if self.limit is None else f"LMT {self.limit:.2f}") if with_price else ""
        if self.kind == "exit":
            q = ("all" if self.qty is None else str(self.qty)) if self.frac is None else f"{self.frac:.0%}"
            if self.keep is not None:
                q = f"all but {self.keep}"
            what = " ".join(x for x in [self.symbol, "spread" if self.spread else "",
                                        {"C": "calls", "P": "puts"}.get(self.right, ""), fecha] if x)
            return f"CLOSE {q} · {what}" + (f" · {px}" if px else "")
        legs = " / ".join(f"{'+' if lg.action == 'BUY' else '−'}{lg.strike:g}{lg.right}"
                          for lg in self.legs)
        return f"BUY {self.qty} {self.symbol} {legs} {fecha}" + (f" · {px}" if px else "")


def manage_exit(entry: Call, raw: str) -> Call:
    """'manage' citando una call: propuesta de cerrar TODA la posicion de ese contrato."""
    c = Call(raw=raw, kind="exit", symbol=entry.symbol, candidates=[entry.symbol],
             expiry=entry.expiry, right=entry.legs[0].right if entry.legs else "",
             spread=entry.spread, fraction="full", qty=None, limit=None)
    c.legs = [Leg(lg.strike, lg.right, "") for lg in entry.legs]  # pistas para encontrarla
    c.notes.append("MANAGE: he says it may be time to close it")
    return c


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def add_trading_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _roll(month: int, day: int, today: date) -> date:
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


roll_date = _roll


def _price(token: str) -> tuple[float | None, str | None]:
    if "." in token:
        return float(token), None
    n = int(token)
    if n < 10:
        return None, f"ambiguous price '{token}' (0.0{n} or {n}.00?)"
    return n / 100, None


def _cut(s: str, m: re.Match) -> str:
    return s[:m.start()] + " " + s[m.end():]


def parse(text: str, channel_action: str, today: date, default_qty: int = 1) -> Call | None:
    """channel_action: 'BUY' (canal de entradas) o 'CLOSE' (canal de salidas).
    Devuelve None si el mensaje no es ni call, ni aviso, ni operacion."""
    t = re.sub(r"[*_`~|>]", " ", text.upper())
    first_line = t.strip().split("\n", 1)[0]
    t = re.sub(r"\s+", " ", PCT_RE.sub(" ", t)).strip()
    words = set(re.findall(r"[A-Z0-9]+", t))
    c = Call(raw=text)

    # Accion
    if words & EXIT_WORDS:
        c.kind = "exit"
    elif words & ENTRY_WORDS:
        c.kind = "entry"
    else:
        c.kind = "entry" if channel_action == "BUY" else "exit"

    # Precio (se quita del texto para no confundirlo con strikes o fechas)
    work = t
    if words & MARKET_WORDS:
        c.limit = None
    else:
        for rx in PRICE_RES:
            if m := rx.search(work):
                c.limit, err = _price(m.group(1))
                if err:
                    c.errors.append(err)
                work = _cut(work, m)
                break

    # Fraccion de salida (1/2, 1/4...) antes de buscar strikes/fechas
    if m := FRACTION_RE.search(work):
        c.fraction = "partial"
        work = _cut(work, m)

    # Strikes
    has_spread = "SPREAD" in words or "SPREADS" in words
    right = next((RIGHT_WORDS[w] for w in ("CALL", "CALLS", "PUT", "PUTS") if w in words), "")
    strikes: list[float] = []
    if has_spread and (m := PAIR_RE.search(work)):
        strikes = [float(m.group(1)), float(m.group(2))]
        work = _cut(work, m)
    elif m := STRIKE_RE.search(work):
        strikes = [float(m.group(1))]
        right = m.group(2)[0]
        work = _cut(work, m)

    # Vencimiento
    if m := DTE_RE.search(work):
        n = int(m.group(1))
        c.expiry = add_trading_days(today, n).strftime("%Y%m%d")
        c.notes.append(f"{n}DTE → {add_trading_days(today, n):%d/%m/%Y}")
        work = _cut(work, m)
    elif (m := SLASH_DATE_RE.search(work)) and 1 <= int(m.group(1)) <= 12:
        mo, dy, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            d = date(int(yr) + (2000 if len(yr) == 2 else 0), mo, dy) if yr else _roll(mo, dy, today)
            c.expiry = d.strftime("%Y%m%d")
        except ValueError:
            c.errors.append(f"invalid date: {m.group(0)}")
        work = _cut(work, m)
    elif m := MONTH_RE.search(work):
        mo = MONTHS[m.group(1)]
        day, year = m.group(2) or m.group(4), m.group(3)
        if day and int(day) > 31:  # "DEC 80": es un strike sin C/P, no un dia
            day = None
        try:
            if day and not year:
                d = _roll(mo, int(day), today)
            else:
                y = int(year) if year else (today.year if mo >= today.month else today.year + 1)
                d = third_friday(y, mo)
                if d < today:
                    d = third_friday(y + 1, mo)
                c.notes.append(f"month only → monthly {d:%d/%m/%Y}")
            c.expiry = d.strftime("%Y%m%d")
        except ValueError:
            c.errors.append(f"invalid date: {m.group(0)}")
        work = _cut(work, m)

    # Tickers (solo de la primera linea: el resto suele ser comentario)
    c.candidates = [w for w in TICKER_RE.findall(first_line) if w not in STOP]
    c.symbol = c.candidates[0] if c.candidates else ""

    # Avisos: correcciones / cancelaciones
    if words & NOTICE_WORDS and not strikes:
        c.kind = "notice"
        return c

    if c.kind == "exit":
        if "SHORT" in words and "LEG" in words:
            c.errors.append("leg adjustment (closing the short leg): do it manually")
        if not c.candidates:
            return None
        c.spread, c.right = has_spread, right
        c.legs = [Leg(s, right, "") for s in strikes]
        if not c.fraction:
            c.fraction = "partial" if words & PARTIAL_WORDS and not words & FULL_WORDS else "full"
        c.qty = 1 if c.fraction == "partial" else None
        if c.limit is None and not words & MARKET_WORDS:
            c.notes.append("no price → market")
        return c

    # ---- entrada
    if not strikes:
        if right or has_spread:
            c.errors.append("call without strike")
        elif words & ENTRY_WORDS and c.symbol and c.expiry:
            c.errors.append("missing CALL or PUT")
        elif words & ENTRY_WORDS and c.symbol and c.limit is not None:
            c.kind = "stock"
            return c
        else:
            return None
    if not c.symbol:
        c.errors.append("ticker not found")
    if c.symbol in FUTURES:
        c.errors.append(f"{c.symbol} is a future: futures options are not copied")
    if "SHORT" in words or "STO" in words:
        c.errors.append("asks to open a SHORT: shorts are never opened")
    if not right:
        c.errors.append("missing CALL or PUT")

    if len(strikes) == 2:
        lo, hi = sorted(strikes)
        buy, sell = (lo, hi) if right == "C" else (hi, lo)  # spread de debito
        if strikes[0] != buy:
            c.notes.append("strikes in unusual order: applying a debit spread")
        c.spread = True
        c.legs = [Leg(buy, right, "BUY"), Leg(sell, right, "SELL")]
    elif strikes:
        c.legs = [Leg(strikes[0], right, "BUY")]

    if not c.expiry:
        c.expiry = today.strftime("%Y%m%d")
        c.notes.append("no date → 0DTE (today)")
    m = QTY_RE.search(t)
    c.qty = int(m.group(1)) if m else default_qty
    if c.limit is None and not words & MARKET_WORDS:
        c.notes.append("no price → market")
    return c
