"""Drawer «Trade» (Trades › Trade): operar a mano una operación, al estilo del panel de órdenes de IBKR. Se abre por
la derecha encima de la app; se cierra con la flecha de arriba a la izquierda, con Esc o pulsando fuera.

Mientras está abierto pide bid/ask cada segundo (y solo entonces). Las acciones son las mismas que los botones de
las tarjetas de Telegram (las hace el motor en el servidor: manual.py) y mandan sobre las reglas automáticas de esa
operación. Comprar y vender, a mercado o con una ORDEN a tu precio (límite: la de compra vale el día; la de venta
queda como tu take profit). No decide nada: manda lo que pulsas y enseña lo que responde el servidor."""
import re

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .. import theme
from ..widgets import Segmented, button, hbox, label, provider_chip, tag, vbox, widget
from .trades import STATUS, contract_label, money, when

WIDTH = 440
STOP_PCTS = (-10, -20, -30, -50)
TP_PCTS = (25, 50, 100, 200)


def parse_price(text: str) -> float | None:
    """El precio de una orden límite (0.85 · 0,85): > 0, o None."""
    try:
        px = round(float((text or "").strip().replace(",", ".")), 2)
    except ValueError:
        return None
    return px if px > 0 else None


def parse_level(text: str, kind: str) -> dict | None:
    """Lo que escribes en «custom»: un precio (0.85) o un % (20% · -20% · +20%). En el SL un % sin signo es de
    PÉRDIDA (como en las tarjetas: «30%» = −30 %); en el TP, de ganancia."""
    t = (text or "").strip().replace(",", ".")
    m = re.fullmatch(r"([+-]?)\s*(\d+(?:\.\d+)?)\s*%|%\s*([+-]?)(\d+(?:\.\d+)?)", t)
    if m:
        sign = m.group(1) or m.group(3) or ""
        val = float(m.group(2) or m.group(4))
        if kind == "stop" and sign != "+":
            val = -val
        elif kind == "tp" and sign == "-":
            val = -val
        return {"pct": val}
    try:
        px = float(t)
    except ValueError:
        return None
    return {"price": px} if px > 0 else None


class TradeDrawer(QWidget):
    """Capa sobre toda la ventana: fondo oscurecido (pulsar = cerrar) + panel a la derecha."""
    quote_requested = Signal(str)                # order_ref
    action_requested = Signal(str, str, object)  # order_ref, acción, valor
    closed = Signal(str)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.trade: dict | None = None
        self.quote: dict | None = None
        self.waiting = False
        self.busy = False
        self.hide()
        self.setAttribute(Qt.WA_StyledBackground, True)  # el fondo oscurecido (lo de detrás, «apagado»)
        self.setObjectName("DrawerShade")
        self.panel = QFrame(self)
        self.panel.setObjectName("Drawer")
        self.scroll = QScrollArea(self.panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.scroll)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.note_text = ""

    # --- abrir / cerrar ---------------------------------------------------------------------------
    def open(self, trade: dict, provider_name: str):
        self.trade, self.name, self.quote, self.note_text = trade, provider_name, None, ""
        self.waiting = self.busy = False
        c = theme.current
        self.setStyleSheet("QWidget#DrawerShade { background: rgba(0, 0, 0, 0.35); }")
        self.panel.setStyleSheet(f"QFrame#Drawer {{ background: {c['bg']}; border-left: 1px solid {c['line2']}; }}"
                                 f"QFrame#Drawer QScrollArea, QFrame#Drawer QScrollArea > QWidget > QWidget "
                                 f"{{ background: {c['bg']}; }}")
        self._layout()
        self.paint()
        self.show()
        self.raise_()
        self.setFocus()
        self._tick()
        self.timer.start(1000)

    def close_drawer(self):
        if self.trade is None:
            return
        ref = self.trade["order_ref"]
        self.timer.stop()
        self.trade = None
        self.hide()
        self.closed.emit(ref)

    def _layout(self):
        self.setGeometry(self.parentWidget().rect())
        self.panel.setGeometry(QRect(self.width() - WIDTH, 0, WIDTH, self.height()))

    def resizeEvent(self, e):
        self.panel.setGeometry(QRect(self.width() - WIDTH, 0, WIDTH, self.height()))
        super().resizeEvent(e)

    def mousePressEvent(self, e):
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_drawer()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_drawer()
        else:
            super().keyPressEvent(e)

    # --- datos ------------------------------------------------------------------------------------
    def _tick(self):
        if self.trade is not None and not self.waiting:
            self.waiting = True
            self.quote_requested.emit(self.trade["order_ref"])

    def set_quote(self, ref: str, quote: dict | None, error: str = ""):
        if self.trade is None or ref != self.trade["order_ref"]:
            return
        self.waiting = False
        if quote is None:
            self.note_text = self.note_text or f"✕  {error}"
        first = self.quote is None
        old = self.quote
        self.quote = quote or self.quote
        same_shape = old and quote and all(old.get(k) == quote.get(k) for k in ("status", "held", "sl", "tp1", "tp2"))
        if first or not same_shape:
            self.paint()  # cambió la posición: se rehace todo
        else:
            self._paint_prices()  # solo precios: no se tocan los campos que estés escribiendo

    def set_result(self, ref: str, ok: bool, text: str):
        if self.trade is None or ref != self.trade["order_ref"]:
            return
        self.busy = False
        self.note_text = text if ok else f"✕  {text}"
        self.paint()

    def _send(self, action: str, value=None):
        if self.trade is None or self.busy:
            return
        self.busy = True
        self.note_text = "Sending…"
        self.paint()
        self.action_requested.emit(self.trade["order_ref"], action, value)

    # --- pintar -----------------------------------------------------------------------------------
    def paint(self):
        t, q = self.trade, self.quote or {}
        if t is None:
            return
        page = QWidget()
        lay = vbox(spacing=16, margins=(20, 16, 20, 20))
        page.setLayout(lay)
        back = button("", icon_name="back")
        back.setFixedWidth(36)
        back.setToolTip("Close (Esc)")
        back.clicked.connect(self.close_drawer)
        status = q.get("status") or t["status"]
        st_name, st_kind = STATUS.get(status, STATUS["error"])
        account = q.get("account") or t.get("account") or "paper"
        title = contract_label(t.get("contract", ""), when(t.get("opened_at")))
        lay.addLayout(hbox(back, label(title, "h2"), None, spacing=10))
        lay.addLayout(hbox(provider_chip(self.name, t["provider_id"]),
                           tag("Live account" if account == "live" else "Paper account",
                               "bad" if account == "live" else "neutral"),
                           tag(st_name, st_kind), None, spacing=8))
        self.prices = QFrame()
        self.prices.setObjectName("DrawerCard")
        self.prices.setLayout(vbox(spacing=0, margins=(16, 14, 16, 14)))
        lay.addWidget(self.prices)
        self._paint_prices()
        is_open = status == "open" and (q.get("held") or t.get("held"))
        if self.note_text:
            ok = not self.note_text.startswith(("✕", "⚠", "❌", "Cancelled"))
            note = label(self.note_text, "small", None if self.note_text == "Sending…" else "good" if ok else "bad",
                         wrap=True)
            note.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(note)
        if is_open:
            lay.addWidget(self._sell_card(q.get("held") or t.get("held") or 0))
            lay.addWidget(self._level_card("stop", q))
            lay.addWidget(self._level_card("tp", q))
            lay.addWidget(self._buy_card("Buy more"))
        else:
            lay.addWidget(self._buy_card("Buy now"))
        lay.addWidget(label("What you do here overrides the automatic rules for this trade: Signa stops moving its "
                            "stop and take profit. Everything is logged in the trade's timeline.", "small",
                            wrap=True))
        lay.addStretch(1)
        self.scroll.setWidget(page)

    def _paint_prices(self):
        outer = self.prices.layout()
        while outer.count():  # un único bloque dentro: se cambia entero cada segundo
            old = outer.takeAt(0).widget()
            if old:
                old.hide()
                old.deleteLater()
        inner = QWidget()
        box = vbox(spacing=6)
        inner.setLayout(box)
        outer.addWidget(inner)
        t, q = self.trade, self.quote or {}
        bid, ask = q.get("bid"), q.get("ask")

        def fmt(v):
            return f"{v:.2f}" if v is not None else "—"
        mid = round((bid + ask) / 2, 3) if bid is not None and ask is not None else None
        live = "●" if self.quote and bid is not None else "○"
        box.addLayout(hbox(self._price("Bid", fmt(bid), "bid"), self._price("Ask", fmt(ask), "ask"),
                           self._price("Mid", fmt(mid)), label(live, "small"), spacing=18))
        entry = q.get("entry") or t.get("entry_px")
        held, qty = q.get("held", t.get("held")), q.get("qty", t.get("qty"))
        if entry:
            un = q.get("unrealized")
            pct = f" ({bid / entry - 1:+.1%})" if bid is not None and held else ""
            tone = "good" if (un or 0) > 0 else "bad" if (un or 0) < 0 else None
            box.addWidget(label(f"Unrealized  {money(un) if un is not None else '—'}{pct}", "name", tone))
            parts = [f"Held {held}/{qty}" if held != qty else f"Held {held}", f"Entry {entry:.2f}"]
            if q.get("realized"):
                parts.append(f"Realized {money(q['realized'])}")
            box.addWidget(label(" · ".join(parts), "small"))
            levels = [f"SL {q['sl']:.2f}" if q.get("sl") else "No SL",
                      f"TP {(q.get('tp2') if q.get('tp1_done') else q.get('tp1')):.2f}"
                      if (q.get("tp2") if q.get("tp1_done") else q.get("tp1")) else "No TP"]
            if q.get("reason") == "manual":
                levels.append("manual control")
            box.addWidget(label(" · ".join(levels), "small"))
        elif t.get("exit_reason"):
            box.addWidget(label(t["exit_reason"], "small", wrap=True))

    @staticmethod
    def _price(name: str, value: str, tone: str | None = None) -> QWidget:
        return widget(vbox(label(name, "small"), label(value, "h2", tone), spacing=0))

    @staticmethod
    def _card(title: str, body: QWidget, action: QWidget | None = None) -> QFrame:
        card = QFrame()
        card.setObjectName("DrawerCard")
        card.setLayout(vbox(hbox(label(title, "sec"), None, *([action] if action else [])), body, spacing=10,
                            margins=(16, 14, 16, 16)))
        return card

    @staticmethod
    def _options(items: list[tuple[str, object]], on_click) -> tuple[QWidget, list]:
        """Fila de opciones del MISMO ancho que llena la tarjeta (como los botones de cantidad de IBKR)."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        btns = []
        for i, (text, value) in enumerate(items):
            b = button(text, "opt")
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, v=value: on_click(v))
            grid.addWidget(b, 0, i)
            grid.setColumnStretch(i, 1)
            btns.append(b)
        return widget(grid), btns

    def _order_type(self, side: str) -> tuple[Segmented, QLineEdit]:
        """Market | Limit + el precio de la orden (al pasar a Limit se rellena con el precio de ahora)."""
        seg = Segmented([("market", "Market"), ("limit", "Limit")], "market")
        price = QLineEdit()
        price.setPlaceholderText("Limit price e.g. 0.85")
        price.setFixedWidth(150)
        price.hide()

        def switch(key):
            price.setVisible(key == "limit")
            q = self.quote or {}
            now = q.get("bid") if side == "sell" else q.get("ask")
            if key == "limit" and not price.text() and now:
                price.setText(f"{now:.2f}")
        seg.changed.connect(switch)
        return seg, price

    def _sell_card(self, held: int) -> QFrame:
        step = Stepper(1, max(1, held), max(1, held))
        go = button("", "sell")
        kind, price = self._order_type("sell")
        hint = label("", "small", wrap=True)
        hint.hide()
        items = {}  # sin repetidos: con 1 solo «All»; con 2, «1» y «All»
        for text, v in (("All", held), ("Half", max(1, held // 2)), ("1", 1)):
            items.setdefault(v, text)
        items = sorted(((t, v) for v, t in items.items()), key=lambda x: x[1])
        values = [v for _t, v in items]
        opts, btns = self._options(items, step.set)
        for b in btns:
            b.setCheckable(True)

        def sync(*_a):
            v = step.value
            px = parse_price(price.text())
            if kind.value == "limit":
                go.setText(f"Sell {v} at {px:.2f} (limit)" if px else f"Sell {v} at your price")
                bid = (self.quote or {}).get("bid")
                hint.setText("Works as your take profit order: it replaces the current one and stays until it fills."
                             + (" At or below the bid it fills now." if px and bid and px <= bid else ""))
            else:
                go.setText(f"Sell {v} at market")
            hint.setVisible(kind.value == "limit")
            for b, val in zip(btns, values):
                b.setChecked(val == v)
        step.changed.connect(sync)
        kind.changed.connect(sync)
        price.textChanged.connect(sync)
        sync()

        def submit():
            if kind.value != "limit":
                return self._send("sell", step.value)
            px = parse_price(price.text())
            if px is None:
                hint.setText("Type the price of your sell order (e.g. 0.85)")
                hint.show()
                return
            self._send("sell_limit", {"qty": step.value, "price": px})
        go.clicked.connect(submit)
        price.returnPressed.connect(submit)
        return self._card(f"Sell · you hold {held}", widget(vbox(hbox(opts, step, spacing=10),
                                                                 hbox(kind, None, price), hint, go, spacing=10)))

    def _level_card(self, kind: str, q: dict) -> QFrame:
        entry = q.get("entry")
        items = [("BE", {"be": True})] if kind == "stop" else []
        items += [(f"{p:+d}%", {"pct": p}) for p in (STOP_PCTS if kind == "stop" else TP_PCTS)]
        opts, btns = self._options(items, lambda v: self._send(kind, v))
        for b, (_t, v) in zip(btns, items):
            if not entry:
                continue
            b.setToolTip(f"Break-even: stop at your entry ({entry:.2f})" if v.get("be")
                         else f"→ {entry * (1 + v['pct'] / 100):.2f}")
        custom = QLineEdit()
        custom.setPlaceholderText("Price 0.85 · or % " + ("(30% = 30% loss)" if kind == "stop" else "(+50%)"))
        hint = label("", "small")
        hint.hide()

        def preview(text):
            v = parse_level(text, kind)
            if v is None or not entry:
                hint.hide()
                return
            px = v.get("price") or entry * (1 + v["pct"] / 100)
            hint.setText(f"→ {px:.2f} ({px / entry - 1:+.0%} on your entry {entry:.2f})")
            hint.show()
        custom.textChanged.connect(preview)
        set_btn = button("Set", "primary")
        set_btn.setFixedWidth(64)

        def submit():
            v = parse_level(custom.text(), kind)
            if v is None:
                hint.setText("Type a price (0.85) or a % (20%)")
                hint.show()
                return
            self._send(kind, v)
        set_btn.clicked.connect(submit)
        custom.returnPressed.connect(submit)
        has = q.get("sl") if kind == "stop" else (q.get("tp2") if q.get("tp1_done") else q.get("tp1"))
        remove = None
        if has:
            remove = button("Remove", "quiet")
            remove.setToolTip("Remove the stop loss" if kind == "stop" else "Remove the take profit")
            remove.clicked.connect(lambda: self._send("stop_remove" if kind == "stop" else "tp_remove"))
        now = f" · now {has:.2f}" if has else " · none"
        return self._card(("Stop loss" if kind == "stop" else "Take profit") + now,
                          widget(vbox(opts, hbox(custom, set_btn, spacing=8), hint, spacing=10)), remove)

    def _buy_card(self, verb: str) -> QFrame:
        mode = {"v": "contracts"}
        seg = Segmented([("contracts", "Contracts"), ("amount", "Amount $")], "contracts")
        step = Stepper(1, 500, int(self.trade.get("qty") or 1))
        amount = QLineEdit()
        amount.setPlaceholderText("$ e.g. 500")
        amount.setFixedWidth(120)
        amount.hide()
        hint = label("", "small", wrap=True)
        go = button(f"{verb} at market", "buy")
        kind, price = self._order_type("buy")

        def usd():
            try:
                return float(amount.text().replace(",", "."))
            except ValueError:
                return None

        def estimate(*_a):
            limit = kind.value == "limit"
            px = parse_price(price.text()) if limit else (self.quote or {}).get("ask")
            at = f"at {px:.2f}" if limit and px else f"at ask {px:.2f}" if px else ""
            go.setText(f"{verb} at {px:.2f} (limit)" if limit and px else f"{verb} at your price" if limit
                       else f"{verb} at market")
            if not px:
                hint.setText("Type the price of your buy order (e.g. 0.85)" if limit else "Waiting for the ask…")
                return
            if mode["v"] == "amount":
                text = (f"≈ {int(usd() // (px * 100))} contracts {at}" if usd() else
                        f"1 contract ≈ ${px * 100:,.0f} {at}")
            else:
                text = f"≈ ${step.value * px * 100:,.0f} {at}"
            ask = (self.quote or {}).get("ask")
            if limit:
                text += (" · at or above the ask it fills now" if ask and px >= ask else
                         " · buy order valid today: if it fills, Signa adds it with its stop loss and take profit")
            hint.setText(text)

        def switch(key):
            mode["v"] = key
            step.setVisible(key == "contracts")
            amount.setVisible(key == "amount")
            estimate()
        seg.changed.connect(switch)
        step.changed.connect(estimate)
        amount.textChanged.connect(estimate)
        kind.changed.connect(estimate)
        price.textChanged.connect(estimate)

        def submit():
            extra = {}
            if kind.value == "limit":
                px = parse_price(price.text())
                if px is None:
                    hint.setText("Type the price of your buy order (e.g. 0.85)")
                    return
                extra = {"limit": px}
            if mode["v"] == "amount":
                if not usd():
                    hint.setText("Type an amount in $")
                    return
                self._send("buy", {"amount": usd(), **extra})
            else:
                self._send("buy", {"contracts": step.value, **extra})
        go.clicked.connect(submit)
        price.returnPressed.connect(submit)
        estimate()
        return self._card(verb, widget(vbox(hbox(seg, None, step, amount), hbox(kind, None, price), hint, go,
                                            spacing=10)))


class Stepper(QWidget):
    """−  N  +  (cantidad de contratos)."""
    changed = Signal(int)

    def __init__(self, low: int, high: int, value: int):
        super().__init__()
        self.low, self.high, self.value = low, high, max(low, min(high, value))
        minus, plus = button("−", "step"), button("+", "step")
        self.text = label(str(self.value), "stepval")
        minus.clicked.connect(lambda: self.set(self.value - 1))
        plus.clicked.connect(lambda: self.set(self.value + 1))
        self.setLayout(hbox(minus, self.text, plus, spacing=4))

    def set(self, v: int):
        v = max(self.low, min(self.high, int(v)))
        self.value = v
        self.text.setText(str(v))
        self.changed.emit(v)
