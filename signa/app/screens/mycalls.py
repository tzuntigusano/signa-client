"""My calls: montar TU propia call (Call/Put, ticker, strike, vencimiento, contratos) y comprarla a mercado en Paper o
Live, con un SL opcional en %. La compra la hace el servidor con el motor del copiador (own_calls.py); desde ahí la
operación sale en Trades y se gestiona con «Trade» como las de los proveedores. No decide nada: manda lo que pones.

«Save trade» la guarda en una lista (solo en este PC, my_calls.json) para comprarla luego con un clic: a mercado o
con una orden de compra a tu precio, con los contratos que elijas en ese momento."""
import json
from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QVBoxLayout

from ...core.logs import HOME
from ...core.market import NY
from ..widgets import Card, Segmented, button, hbox, label, vbox, widget
from .base import Page
from .drawer import Stepper

DAYS_AHEAD = 10   # días hábiles uno a uno (0DTE, 1DTE…)
WEEKS_AHEAD = 26  # y después, los viernes hasta medio año
QUOTE_EVERY = 2000  # ms: el precio del contrato mientras estás en la pantalla
MAX_CONTRACTS = 50
SAVED = HOME / "my_calls.json"  # tus calls guardadas (solo en este PC)
KEYS = ("account", "right", "symbol", "strike", "expiry", "contracts", "sl_pct")  # lo que se guarda de cada una


def load_saved() -> list[dict]:
    try:
        data = json.loads(SAVED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict) and all(k in d for k in KEYS)] if isinstance(data, list) else []


def store_saved(items: list[dict]):
    SAVED.parent.mkdir(parents=True, exist_ok=True)
    SAVED.write_text(json.dumps(items, indent=1), encoding="utf-8")


def call_name(o: dict) -> str:
    """«SPY 770C Fri Oct 30» (el año solo si no es este)."""
    day = datetime.strptime(o["expiry"], "%Y%m%d").date()
    when = day.strftime("%a %b %d" if day.year == datetime.now(NY).year else "%a %b %d, %Y")
    return f"{o['symbol']} {o['strike']:g}{o['right']} {when}"


def expiries(today: date) -> list[tuple[str, str]]:
    """(YYYYMMDD, texto) de los vencimientos que se ofrecen: hoy y los próximos días hábiles, luego viernes."""
    out, d = [], today
    while len(out) < DAYS_AHEAD:
        if d.weekday() < 5:
            text = d.strftime("%a %b %d")
            out.append((d.strftime("%Y%m%d"), f"{text} · 0DTE" if d == today else text))
        d += timedelta(days=1)
    last = d - timedelta(days=1)
    fri = last + timedelta(days=(4 - last.weekday()) % 7 or 7)
    for _ in range(WEEKS_AHEAD):
        out.append((fri.strftime("%Y%m%d"), fri.strftime("%a %b %d, %Y")))
        fri += timedelta(days=7)
    return out


def parse_strike(text: str) -> float | None:
    try:
        k = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return k if k > 0 else None


def parse_sl(text: str) -> float | None | str:
    """El SL en % de pérdida («50», «-50», «50%»): None = sin SL; "bad" = no se entiende."""
    t = (text or "").strip().replace(",", ".").replace("%", "").lstrip("-").strip()
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return "bad"
    return v if 0 < v <= 95 else "bad"


class MyCallsScreen(Page):
    quote_requested = Signal(dict)  # el contrato: la app pide su bid/ask al servidor
    buy_requested = Signal(dict)    # comprar (la ventana lo confirma antes)

    def __init__(self):
        super().__init__("My calls", "Build your own call and buy it at market")
        self.quote: dict | None = None
        self.quoted_for: tuple | None = None  # el contrato del último precio pedido
        self.waiting = False
        self.busy = False
        self.saved = load_saved()
        self.saved_btns: list = []  # los botones de comprar de la lista (se apagan mientras se manda una compra)
        self.from_saved = False  # la compra en curso salió de la lista: su resultado se enseña ahí

        card = Card()
        self.account = Segmented([("paper", "Paper"), ("live", "Live")], "paper")
        self.right = Segmented([("C", "Call"), ("P", "Put")], "C")
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("SPY")
        self.symbol.setFixedWidth(140)
        self.symbol.textEdited.connect(lambda t: self.symbol.setText(t.upper()))
        self.strike = QLineEdit()
        self.strike.setPlaceholderText("770")
        self.strike.setFixedWidth(140)
        self.expiry = QComboBox()
        self.expiry.setMinimumWidth(200)
        self._fill_expiries()
        self.contracts = Stepper(1, MAX_CONTRACTS, 1)
        self.sl = QLineEdit()
        self.sl.setPlaceholderText("e.g. 50 (empty = none)")
        self.sl.setFixedWidth(180)
        card.add_row("Account", "Live sends a real order with real money (you confirm it first).", self.account)
        card.add_row("Type", None, self.right)
        card.add_row("Ticker", None, self.symbol)
        card.add_row("Strike", None, self.strike)
        card.add_row("Expiry", None, self.expiry)
        card.add_row("Contracts", None, self.contracts)
        card.add_row("Stop loss %", "Below your entry, placed in IBKR as soon as it fills. Empty = no stop loss: "
                     "you manage it from Trades.", self.sl)
        self.add(card)

        self.prices = Card(16)
        self.bid_lb, self.ask_lb, self.cost_lb = label("—", "h2"), label("—", "h2"), label("—", "h2")
        self.live_lb = label("", "small")
        self.prices.add(hbox(self._price("Bid", self.bid_lb), self._price("Ask", self.ask_lb),
                             self._price("Cost ≈", self.cost_lb), None, self.live_lb, spacing=28))
        self.go = button("Buy at market", "buy")
        self.go.clicked.connect(self._buy)
        self.keep = button("Save trade")
        self.keep.setMinimumHeight(38)
        self.keep.setToolTip("Keep this call in the list below to buy it later with one click")
        self.keep.clicked.connect(self._save)
        self.note = label("", "small", wrap=True)
        self.prices.add(hbox(self.go, self.keep, spacing=10))
        self.prices.add(self.note)
        self.add(self.prices)

        self.add(label("Saved trades", "sec"))
        self.saved_card = Card(16)
        self.saved_list = QVBoxLayout()
        self.saved_list.setSpacing(10)
        self.saved_card.add(self.saved_list)
        self.saved_note = label("", "small", wrap=True)
        self.saved_card.add(self.saved_note)
        self.add(self.saved_card)
        self.add(label("Bought at market during regular hours (9:30–16:00 ET). Your risk limits (Settings) apply. "
                       "0DTE leftovers are sold at 15:55 ET. After that it's a trade like any other: sell, move the "
                       "stop or set a take profit from Trades › Trade.", "small", wrap=True))
        self.finish()

        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(500)
        self.debounce.timeout.connect(self._ask_quote)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._ask_quote)
        for w in (self.symbol, self.strike):
            w.textChanged.connect(self._changed)
        self.sl.textChanged.connect(lambda _t: self._paint())
        self.expiry.currentIndexChanged.connect(self._changed)
        self.account.changed.connect(self._changed)
        self.right.changed.connect(self._changed)
        self.contracts.changed.connect(lambda _v: self._paint())
        self._render_saved()
        self._paint()

    @staticmethod
    def _price(name: str, value) -> object:
        return widget(vbox(label(name, "small"), value, spacing=0))

    def _fill_expiries(self):
        today = datetime.now(NY).date()
        if getattr(self, "expiries_for", None) == today:
            return
        self.expiries_for = today
        keep = self.expiry.currentData()
        self.expiry.blockSignals(True)
        self.expiry.clear()
        for value, text in expiries(today):
            self.expiry.addItem(text, value)
        i = self.expiry.findData(keep)
        self.expiry.setCurrentIndex(max(0, i))
        self.expiry.blockSignals(False)

    # --- la orden ---------------------------------------------------------------------------------
    def order(self) -> dict | None:
        """La call del formulario, o None si falta algo (ticker o strike)."""
        sym, k = self.symbol.text().strip().upper().lstrip("$"), parse_strike(self.strike.text())
        if not sym or k is None:
            return None
        sl = parse_sl(self.sl.text())
        return {"account": self.account.value, "right": self.right.value, "symbol": sym, "strike": k,
                "expiry": self.expiry.currentData(), "contracts": self.contracts.value,
                "sl_pct": None if sl == "bad" else sl}

    @staticmethod
    def key(o: dict | None) -> tuple | None:
        return (o["account"], o["right"], o["symbol"], o["strike"], o["expiry"]) if o else None

    def name(self, o: dict) -> str:
        return f"{o['symbol']} {o['strike']:g}{o['right']} {self.expiry.currentText().split(' · ')[0]}"

    # --- precios ----------------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        self._fill_expiries()
        self.timer.start(QUOTE_EVERY)
        self._ask_quote()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def _changed(self, *_a):
        if self.key(self.order()) != self.quoted_for:
            self.quote, self.quoted_for = None, None
            self.note.setText("")
        self._paint()
        self.debounce.start()

    def _ask_quote(self):
        o = self.order()
        if o is None or self.waiting or not self.isVisible():
            return
        self.waiting = True
        self.asked = self.key(o)
        self.quote_requested.emit(o)

    def set_quote(self, quote: dict | None, error: str = ""):
        self.waiting = False
        asked = getattr(self, "asked", None)
        if asked != self.key(self.order()):  # cambiaste el contrato mientras llegaba: se pide el nuevo
            self.debounce.start()
            return
        self.quoted_for = asked
        if quote is None:
            self.quote = None
            if not self.busy:
                self.note.setText(f"✕  {error}")
                self.note.setProperty("tone", "bad")
                self.note.style().polish(self.note)
        else:
            self.quote = quote
            if self.note.text().startswith("✕") and not self.busy:
                self.note.setText("")
        self._paint()

    def _paint(self):
        o, q = self.order(), self.quote or {}
        bid, ask = q.get("bid"), q.get("ask")
        self.bid_lb.setText(f"{bid:.2f}" if bid is not None else "—")
        self.ask_lb.setText(f"{ask:.2f}" if ask is not None else "—")
        n = self.contracts.value
        self.cost_lb.setText(f"${ask * 100 * n:,.0f}" if ask else "—")
        self.live_lb.setText("● live" if self.quote and ask is not None else "")
        sl = parse_sl(self.sl.text())
        if self.busy:
            self.go.setText("Sending…")
        elif o is None:
            self.go.setText("Type the ticker and the strike")
        else:
            self.go.setText(f"Buy {n} {self.name(o)} at market" + (" · LIVE" if o["account"] == "live" else ""))
        self.go.setEnabled(o is not None and not self.busy and sl != "bad")
        self.keep.setEnabled(o is not None and sl != "bad")
        for b in self.saved_btns:
            b.setEnabled(not self.busy and b.property("live") is True)
        if sl == "bad":
            self.note.setText("✕  Stop loss: a % between 1 and 95 (e.g. 50), or leave it empty.")

    # --- comprar ----------------------------------------------------------------------------------
    def _buy(self):
        o = self.order()
        if o is None or self.busy:
            return
        o["name"] = self.name(o)
        o["ask"] = (self.quote or {}).get("ask") if self.quoted_for == self.key(o) else None
        self.from_saved = False
        self.buy_requested.emit(o)

    def sending(self):
        self.busy = True
        self._say(self.saved_note if self.from_saved else self.note,
                  "Sending… (it waits for the fill, a few seconds)", "")
        self._paint()

    def set_result(self, ok: bool, text: str):
        self.busy = False
        self._say(self.saved_note if self.from_saved else self.note, text if ok else f"✕  {text}",
                  "good" if ok else "bad")
        self.from_saved = False
        self._paint()

    @staticmethod
    def _say(lb, text: str, tone: str):
        lb.setText(text)
        lb.setProperty("tone", tone)
        lb.style().polish(lb)

    # --- guardadas --------------------------------------------------------------------------------
    def _save(self):
        """La call del formulario, a la lista (la misma call otra vez = se actualizan sus contratos y su SL)."""
        o = self.order()
        if o is None or parse_sl(self.sl.text()) == "bad":
            return
        item = {k: o[k] for k in KEYS}
        same = next((i for i, d in enumerate(self.saved) if self.key(d) == self.key(item)), None)
        if same is None:
            self.saved.insert(0, item)
        else:
            self.saved[same] = item
        store_saved(self.saved)
        self._render_saved()
        self._say(self.saved_note, f"✓  Saved {call_name(item)}", "good")

    def _render_saved(self):
        while self.saved_list.count():
            it = self.saved_list.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.saved_btns = []
        if not self.saved:
            self.saved_list.addWidget(label("Nothing saved yet: build a call above and press «Save trade».",
                                            "small", wrap=True))
            return
        today = datetime.now(NY).strftime("%Y%m%d")
        for i, o in enumerate(self.saved):
            self.saved_list.addWidget(self._saved_row(i, o, o["expiry"] < today))
        self._paint()

    def _saved_row(self, i: int, o: dict, expired: bool):
        info = [o["account"].capitalize(), f"SL −{o['sl_pct']:g}%" if o.get("sl_pct") else "no stop loss"]
        text = vbox(label(call_name(o), "name"), label(" · ".join(info + (["expired"] if expired else [])),
                                                       "small", "bad" if expired else None), spacing=2)
        n = Stepper(1, MAX_CONTRACTS, o.get("contracts") or 1)
        n.changed.connect(lambda v, i=i: self._saved_contracts(i, v))
        market = button("Buy at market" + (" · LIVE" if o["account"] == "live" else ""), "buy")
        market.setFixedWidth(170)  # con y sin «· LIVE», las filas alineadas
        price = QLineEdit()
        price.setPlaceholderText("Price")
        price.setFixedWidth(90)
        order = button("Buy order", "primary")
        order.setMinimumHeight(38)
        order.setToolTip("A buy order at your price (limit, valid today)")
        drop = button("✕", "quiet")
        drop.setToolTip("Remove from the list")
        market.clicked.connect(lambda _=False, i=i, n=n: self._buy_saved(i, n.value, None))
        order.clicked.connect(lambda _=False, i=i, n=n, p=price: self._buy_saved(i, n.value, p.text()))
        price.returnPressed.connect(order.click)
        drop.clicked.connect(lambda _=False, i=i: self._drop(i))
        for b in (market, order):
            b.setProperty("live", not expired)
            self.saved_btns.append(b)
        row = hbox(spacing=10)
        row.addLayout(text, 1)
        for w in (n, market, price, order, drop):
            row.addWidget(w)
        return widget(row)

    def _saved_contracts(self, i: int, v: int):
        if 0 <= i < len(self.saved):
            self.saved[i]["contracts"] = v
            store_saved(self.saved)

    def _drop(self, i: int):
        if 0 <= i < len(self.saved):
            gone = self.saved.pop(i)
            store_saved(self.saved)
            self._render_saved()
            self._say(self.saved_note, f"Removed {call_name(gone)}", "")

    def _buy_saved(self, i: int, n: int, price: str | None):
        """Comprar una guardada: a mercado (price None) o con una orden a tu precio. La ventana lo confirma."""
        if self.busy or not 0 <= i < len(self.saved):
            return
        o = {**self.saved[i], "contracts": n}
        if price is not None:
            limit = parse_strike(price)
            if limit is None:
                self._say(self.saved_note, "✕  Buy order: type your price (e.g. 1.25) next to the button.", "bad")
                return
            o["limit"] = round(limit, 2)
        o["name"], o["ask"] = call_name(o), None
        self.from_saved = True
        self.buy_requested.emit(o)
