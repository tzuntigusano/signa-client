"""Trades: las operaciones REALES de Signa (todas las cuentas y proveedores), también las que no compró y por qué."""
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QHeaderView, QSizePolicy, QVBoxLayout, QWidget

from .. import theme
from ..widgets import Card, Segmented, button, chip_cell, hbox, kpi, label, provider_chip, table, tag, vbox
from .base import Page

STATUS = {"open": ("Open", "accent"), "closed": ("Closed", "neutral"), "skipped": ("Not bought", "warn"),
          "error": ("Error", "bad"), "pending": ("Pending", "neutral")}


def when(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts).astimezone()
    except (TypeError, ValueError):
        return None


def money(v) -> str:
    return "—" if v is None else f"{v:+,.2f}"


def contract_label(text: str, opened: datetime | None = None) -> str:
    """«SPY 769C 20261027» (como lo guarda el motor) -> «SPY 769C 0DTE» o «SPY 769C Oct 30»."""
    parts = (text or "").split()
    if parts and len(parts[-1]) == 8 and parts[-1].isdigit():
        try:
            exp = datetime.strptime(parts[-1], "%Y%m%d").date()
        except ValueError:
            return text
        same = opened is not None and exp == opened.date()
        parts[-1] = "0DTE" if same else exp.strftime("%b %d").replace(" 0", " ")
    return " ".join(parts)


def pnl_pct(t: dict) -> float | None:
    """% sobre lo vendido: precio medio de venta frente a la entrada (en una abierta con el TP1 cobrado, lo de
    ese contrato). Sin ventas, nada."""
    entry, exit_px = t.get("entry_px"), t.get("exit_px")
    if not entry or not exit_px or t["status"] in ("skipped", "error"):
        return None
    return exit_px / entry - 1


def prices(entry, exit_px) -> str:
    if not entry:
        return "—"
    return f"{entry:.2f} → {exit_px:.2f}" if exit_px else f"{entry:.2f}"


# los motivos del motor del copiador, en claro (el texto original va en el tooltip). Lo que no esté aquí se enseña
# tal cual: el motor ya lo escribe en inglés («TP1 +100%», «SL −50%», «chasing: ask …»)
REASONS = {"his exit": "{p} called the exit", "his warning": "{p}'s warning", "his manage": "{p}'s manage",
           "ladder": "Trailing (ladder)", "end of day": "End of day", "stop at break-even": "Stop at break-even",
           "left open by a restart": "Lost track after a restart", "no live quote": "No live price",
           "outside regular session": "Outside market hours", "already holding that contract": "Already holding it",
           "re-entry: same strike still open": "Same strike still open",
           "your sell (Signa)": "You sold it (Trade)", "manual": "Your manual control"}


def reason_label(reason: str | None, provider: str) -> str:
    if not reason:
        return ""
    r = REASONS.get(reason)
    if r:
        return r.format(p=provider)
    if reason.startswith("chasing:"):
        return "Price ran " + reason.split("=", 1)[1].split(" over")[0].strip() if "=" in reason else reason
    if reason.startswith("too expensive"):
        return "Too expensive" + (f" ({reason.split('=', 1)[1].strip()})" if "=" in reason else "")
    if reason.startswith("plan/quote:"):
        return "Couldn't price it: " + reason.split(":", 1)[1].strip()
    return reason[:1].upper() + reason[1:]


MAX_EVENTS = 60
# origen de cada evento del detalle: (texto, tono). Los del proveedor llevan su chip y el tipo de enlace
ENGINE_KINDS = {"plan": ("Signa", "accent"), "skip": ("Skipped", "warn"), "order": ("Order", "accent"),
                "result": ("IBKR", "neutral"), "fill": ("Filled", "good"), "stop": ("SL", "warn"),
                "tp": ("TP", "good"), "protect": ("Order", "neutral"), "sale": ("Sold", "good"),
                "card": ("Card", "neutral"), "error": ("Error", "bad")}
LINKS = {"entry": "call", "reply": "reply", "edit": "edit", "chat": "chat"}


def event_chip(e: dict, provider_name: str, provider_id: str) -> QWidget:
    if e.get("kind") == "provider":
        chip = provider_chip(f"{provider_name} · {LINKS.get(e.get('link'), 'msg')}", provider_id)
    else:
        text, tone = ENGINE_KINDS.get(e.get("kind"), (e.get("kind") or "?", "neutral"))
        chip = tag(text, tone)
    return chip_cell(chip)


class TradesScreen(Page):
    detail_requested = Signal(str)  # order_ref -> la ventana lo pide al servidor
    trade_requested = Signal(str)   # order_ref -> la ventana abre el drawer «Trade»

    def __init__(self):
        super().__init__("Trades")
        self.trades: list[dict] = []
        self.names: dict[str, str] = {}
        self.expanded: set[str] = set()
        self._last = None  # lo último pintado: si llega igual, no se rehace
        self.details: dict[str, list | str | None] = {}  # None = cargando; str = error
        self.period = "today"
        self.seg = Segmented([("today", "Today"), ("week", "Last 7 days"), ("all", "All")], "today")
        self.seg.changed.connect(self._set_period)
        self.bar = hbox(self.seg, None)
        self.add(self.bar)
        self.host = Card()
        self.add(self.host)
        self.finish()
        self.refresh()

    def set_demo(self):
        self.bar.addWidget(tag("Demo data · not your real trades", "warn"))

    def set_data(self, trades: list[dict] | None = None, providers: list[dict] | None = None):
        sig = repr((trades, sorted((p["id"], p["name"]) for p in providers) if providers is not None else None))
        if trades is not None and providers is not None and sig == self._last:
            return  # nada nuevo (llega cada sincronización): no se rehace la tabla
        self._last = sig
        if trades is not None:
            self.trades = trades
            for t in trades:  # una desplegada que sigue viva puede tener novedades: se vuelve a pedir
                if t["order_ref"] in self.expanded and t["status"] == "open":
                    self.details.pop(t["order_ref"], None)
                    self.detail_requested.emit(t["order_ref"])
        if providers is not None:
            self.names = {p["id"]: p["name"] for p in providers}
        self.refresh()

    def _set_period(self, key: str):
        self.period = key
        self.refresh()

    def _visible(self) -> list[dict]:
        now = datetime.now().astimezone()
        out = []
        for t in sorted(self.trades, key=lambda t: when(t.get("opened_at")) or now, reverse=True):
            d = when(t.get("opened_at"))
            if d is None:
                continue
            if self.period == "today" and d.date() != now.date():
                continue
            if self.period == "week" and (now - d).days >= 7:
                continue
            out.append(t)
        return out

    def refresh(self):
        """Rehace la tabla conservando dónde estabas (scroll) y lo desplegado."""
        bar = self.scroll_area.verticalScrollBar()
        pos = bar.value()
        self._paint()
        QTimer.singleShot(0, lambda: bar.setValue(min(pos, bar.maximum())))

    def _paint(self):
        lay = self.host.lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        rows = self._visible()
        if not rows:
            box = vbox(label("No trades yet" if not self.trades else "No trades in this period", "h2"),
                       label("Every order Signa places shows up here with its provider, prices, exit and P&L. Calls "
                             "it doesn't buy are listed too, with the reason.", "mu", wrap=True),
                       spacing=6, margins=(20, 20, 20, 20))
            w = QFrame()
            w.setLayout(box)
            lay.addWidget(w)
            return
        done = [t for t in rows if t["status"] in ("open", "closed")]
        closed = [t for t in done if t["status"] == "closed"]
        pnl = sum(t.get("pnl") or 0 for t in done)
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        strip = QFrame()
        strip.setLayout(hbox(kpi("P&L", money(pnl), "Closed and partial sales", "good" if pnl >= 0 else "bad"),
                             kpi("Trades", str(len(done)), f"{len(done) - len(closed)} still open"),
                             kpi("Winners", f"{wins}/{len(closed)}" if closed else "—", "Closed trades"),
                             kpi("Not bought", str(sum(1 for t in rows if t["status"] == "skipped")),
                                 f"{errors} with errors" if (errors := sum(1 for t in rows if t["status"] == "error"))
                                 else "Price ran, too expensive…", "bad" if errors else None),
                             spacing=12, margins=(12, 12, 12, 12)))
        lay.addWidget(strip)
        data = []
        for t in rows:
            d = when(t.get("opened_at"))
            stamp = "%H:%M" if self.period == "today" else "%b %d %H:%M"
            name = self.names.get(t["provider_id"], t["provider_id"])
            qty = t.get("qty") or 0
            held = t.get("held") or 0
            shown_qty = f"{held}/{qty}" if t["status"] == "open" and held != qty else str(qty or "")
            pnl = t.get("pnl")
            if t["status"] in ("skipped", "error") or t["status"] == "open" and not pnl:
                pnl_text = "—" if t["status"] == "open" else ""  # abierta sin ventas: aún no hay nada realizado
            else:
                pnl_text = money(pnl)
            arrow = "▾ " if t["order_ref"] in self.expanded else "▸ "
            pct = pnl_pct(t)
            data.append([arrow + (d.strftime(stamp).replace(" 0", " ") if d else ""), "",
                         contract_label(t.get("contract", ""), d), shown_qty,
                         prices(t.get("entry_px"), t.get("exit_px")),
                         reason_label(t.get("exit_reason"), name), pnl_text,
                         f"{pct:+.1%}" if pct is not None else "", "", ""])
        tones = {}
        for i, t in enumerate(rows):
            p = t.get("pnl") or 0
            tones[(i, 6)] = tones[(i, 7)] = "good" if p > 0 else "bad" if p < 0 else "mu"
        tbl = table(["Opened", "Provider", "Contract", "Qty", "Entry → exit", "Exit / reason", "P&L", "% P&L",
                     "Status", ""], data, right={3, 6, 7}, tones=tones, stretch=5)
        tbl.horizontalHeader().setMinimumSectionSize(40)
        # anchos fijos ANTES de meter chips y botones: si no, se quedan con el ancho de antes y salen cortados
        for col, w in ((1, 130), (8, 120), (9, 86)):
            tbl.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
            tbl.setColumnWidth(col, w)
        for i, t in enumerate(rows):
            tbl.setCellWidget(i, 1, chip_cell(provider_chip(self.names.get(t["provider_id"], t["provider_id"]),
                                                            t["provider_id"])))
            name, kind = STATUS.get(t["status"], STATUS["error"])
            tbl.setCellWidget(i, 8, chip_cell(tag(name, kind)))
            go = button("Trade", "key")  # igual en todas las filas
            go.setFixedSize(64, 28)
            go.setToolTip("Sell, set your stop loss / take profit or buy more" if t["status"] == "open"
                          else "Buy this contract now")
            go.clicked.connect(lambda _=False, ref=t["order_ref"]: self.trade_requested.emit(ref))
            tbl.setCellWidget(i, 9, chip_cell(go))
            if tbl.item(i, 5):
                tbl.item(i, 5).setToolTip(t.get("exit_reason") or "")
            tbl.item(i, 2).setToolTip(f"{t.get('contract', '')} · "
                                      + ("Live account" if t.get("account") == "live" else "Paper account"))
        tbl.setTextElideMode(Qt.ElideRight)
        tbl.setStyleSheet(f"QTableWidget {{ border: 0; border-top: 1px solid {theme.current['line']}; "
                          "border-radius: 0; }")
        # desplegadas: una fila debajo, a todo lo ancho, con su cronología (de abajo arriba para no mover índices)
        row_of = list(rows)
        for i in reversed(range(len(rows))):
            ref = rows[i]["order_ref"]
            if ref not in self.expanded:
                continue
            tbl.insertRow(i + 1)
            tbl.setSpan(i + 1, 0, 1, tbl.columnCount())
            panel = self._timeline(rows[i])
            tbl.setCellWidget(i + 1, 0, panel)
            tbl.setRowHeight(i + 1, panel.sizeHint().height())
            row_of.insert(i + 1, None)
        tbl.setFixedHeight(tbl.horizontalHeader().height() + sum(tbl.rowHeight(r) for r in range(tbl.rowCount())) + 2)
        tbl.setCursor(Qt.PointingHandCursor)
        tbl.cellClicked.connect(lambda r, _c: self._toggle(row_of[r]) if row_of[r] else None)
        lay.addWidget(tbl)

    # --- detalle de una operación ---------------------------------------------------------------------
    def _toggle(self, t: dict):
        ref = t["order_ref"]
        if ref in self.expanded:
            self.expanded.discard(ref)
        else:
            self.expanded.add(ref)
            if ref not in self.details:
                self.details[ref] = None  # cargando
                self.detail_requested.emit(ref)
        self.refresh()

    def set_detail(self, ref: str, events: list[dict] | None, error: str = ""):
        self.details[ref] = events if events is not None else error or "Couldn't load the details."
        if ref in self.expanded:
            self.refresh()

    def _timeline(self, t: dict) -> QWidget:
        """Cronología: lo que dijo el proveedor (su call, respuestas, ediciones, charla) y lo que hizo Signa."""
        c = theme.current
        box = QFrame()
        box.setStyleSheet(f"QFrame#Timeline {{ background: {c['sub']}; border-top: 1px solid {c['line']}; }}")
        box.setObjectName("Timeline")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(52, 10, 20, 12)
        lay.setSpacing(4)
        ev = self.details.get(t["order_ref"])
        name = self.names.get(t["provider_id"], t["provider_id"])
        if ev is None:
            lay.addWidget(label("Loading what happened…", "mu"))
        elif isinstance(ev, str):
            lay.addWidget(label(ev, "mu"))
        elif not ev:
            lay.addWidget(label("Nothing recorded for this trade.", "mu"))
        else:
            opened = when(t.get("opened_at"))
            for e in ev[:MAX_EVENTS]:
                d = when(e.get("at"))
                stamp = (d.strftime("%H:%M:%S") if d and opened and d.date() == opened.date()
                         else d.strftime("%b %d %H:%M").replace(" 0", " ") if d else "")
                text = " · ".join(x.strip() for x in (e.get("text") or "").splitlines() if x.strip())
                row = QHBoxLayout()
                row.setSpacing(10)
                when_l = label(stamp, "mono")
                when_l.setFixedWidth(96)
                row.addWidget(when_l)
                who = event_chip(e, name, t["provider_id"])
                who.setFixedWidth(160)
                row.addWidget(who)
                txt = label(text, "small")
                txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # se corta, no ensancha la tabla
                txt.setToolTip(e.get("text") or "")
                txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
                row.addWidget(txt, 1)
                lay.addLayout(row)
            if len(ev) > MAX_EVENTS:
                lay.addWidget(label(f"…and {len(ev) - MAX_EVENTS} more", "mu"))
        box.setFixedHeight(lay.sizeHint().height() + 4)
        return box
