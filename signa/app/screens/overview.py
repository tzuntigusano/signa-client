"""Overview: solo datos REALES. Hoy: las calls que han llegado de tus proveedores, tus operaciones (P&L del día y
posiciones abiertas), el estado de la conexión y los AVISOS de Signa (cuadre con IBKR, errores de un motor). Arriba,
«Pause buying» (no compra nada nuevo; lo abierto se sigue gestionando) y «Close all» (vende todo a mercado)."""
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHeaderView, QMessageBox, QWidget

from ..widgets import Card, button, chip_cell, hbox, kpi, label, provider_chip, section, table, tag, vbox, widget
from .base import Page
from .calls import clock, contract_text, kind_chip
from .trades import money

ALERTS = {"warning", "exit", "stop", "manage"}  # = Calls › Alerts
STATUS = {"online": ("Connected", "good"), "connecting": ("Connecting…", "warn"),
          "offline": ("Offline, retrying", "bad"), "rejected": ("Key not valid", "bad")}


def is_today(ts: str | None) -> bool:
    try:
        return datetime.fromisoformat(ts).astimezone().date() == datetime.now().astimezone().date()
    except (TypeError, ValueError):
        return False


class OverviewScreen(Page):
    pause_requested = Signal(bool)  # True = pausar las compras
    close_all_requested = Signal()

    def __init__(self):
        self.pause_btn = button("Pause buying")
        self.close_btn = button("Close all", "danger")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.close_btn.clicked.connect(self._close_all)
        super().__init__("Overview", datetime.now().strftime("%A, %b %d"),
                         right=widget(hbox(self.pause_btn, self.close_btn, spacing=8)))
        self.paused = False
        self.alerts: list[dict] = []
        self.providers: list[dict] = []
        self.signals: list[dict] = []
        self.trades: list[dict] = []
        self.status = "connecting"
        self.host = QWidget()
        self.host.setLayout(vbox(spacing=18))
        self.add(self.host)
        self.finish()
        self.refresh()

    def set_data(self, providers: list[dict] | None = None, signals: list[dict] | None = None,
                 status: str | None = None, trades: list[dict] | None = None, risk: dict | None = None):
        if risk is not None:
            self.paused = bool(risk.get("paused"))
            self.pause_btn.setText("Resume buying" if self.paused else "Pause buying")
            self.pause_btn.setProperty("kind", "primary" if self.paused else None)
            self.pause_btn.style().unpolish(self.pause_btn)
            self.pause_btn.style().polish(self.pause_btn)
        if trades is not None:
            self.trades = trades
        if providers is not None:
            self.providers = providers
        if signals is not None:
            self.signals = signals
        if status is not None:
            self.status = status
        self.refresh()

    def refresh(self):
        lay = self.host.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        names = {p["id"]: p["name"] for p in self.providers}
        today = [s for s in self.signals if is_today(s.get("ts"))]
        entries = [s for s in today if s["kind"] == "entry"]
        alerts = [s for s in today if s["kind"] in ALERTS]
        last = max(today, key=lambda s: s.get("ts") or "") if today else None
        following = [p for p in self.providers if p.get("followed")]
        opened = [t for t in self.trades if t["status"] in ("open", "closed") and is_today(t.get("opened_at"))]
        live = [t for t in self.trades if t["status"] == "open"]
        pnl = sum(t.get("pnl") or 0 for t in opened)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.addWidget(kpi("Calls today", str(len(entries)), "New entries from your providers"), 0, 0)
        grid.addWidget(kpi("Alerts today", str(len(alerts)), "Exits, stops and warnings"), 0, 1)
        grid.addWidget(kpi("Last message", clock(last["ts"]) if last else "—",
                           names.get(last["provider"], last["provider"]) if last else "Nothing yet today"), 0, 2)
        grid.addWidget(kpi("P&L today", money(pnl) if opened else "—",
                           f"{len(opened)} trades · {len(live)} open" if opened or live else "No trades today",
                           ("good" if pnl >= 0 else "bad") if opened else None), 0, 3)
        lay.addWidget(widget(grid))

        left = vbox(section("Today's calls and alerts", self._calls(entries + alerts, names)),
                    section("Open positions", self._positions(live, names)), spacing=18)
        right = vbox(*([section("Signa alerts", self._alerts())] if self.alerts else []),
                     section("Connection", self._connection()), section("Following", self._following(following)),
                     None, spacing=18)
        if self.paused:
            lay.insertWidget(0, label("⏸  Buying is paused: Signa takes no new trades. Your open trades are still "
                                      "managed (stops, take profits, exits).", "name", "warn", wrap=True))
        cols = hbox(spacing=18)
        cols.addLayout(left, 3)
        cols.addLayout(right, 2)
        lay.addWidget(widget(cols))

    @staticmethod
    def _calls(rows: list[dict], names: dict) -> QWidget:
        if not rows:
            card = Card(padding=16)
            card.add(label("No calls yet today. They show up here the moment your providers post them.", "mu",
                           wrap=True))
            return card
        rows = sorted(rows, key=lambda s: s.get("ts") or "", reverse=True)[:10]
        t = table(["Time", "Provider", "Call", "Type"], [[clock(s["ts"]), "", contract_text(s), ""] for s in rows],
                  stretch=2)
        for i, s in enumerate(rows):
            t.setCellWidget(i, 1, chip_cell(provider_chip(names.get(s["provider"], s["provider"]), s["provider"])))
            t.setCellWidget(i, 3, chip_cell(kind_chip(s)))
        for col, w in ((1, 140), (3, 110)):  # columnas de chips: ancho fijo (no tienen texto que medir)
            t.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
            t.setColumnWidth(col, w)
        t.setTextElideMode(Qt.ElideRight)
        return t

    @staticmethod
    def _positions(rows: list[dict], names: dict) -> QWidget:
        if not rows:
            card = Card(padding=16)
            card.add(label("No open positions. When Signa buys a call it shows up here until it's closed.", "mu",
                           wrap=True))
            return card
        t = table(["Provider", "Contract", "Qty", "Entry", "Stop"],
                  [["", r.get("contract", ""), str(r.get("held") or r.get("qty") or ""),
                    f"{r['entry_px']:.2f}" if r.get("entry_px") else "—",
                    f"{r['stop_px']:.2f}" if r.get("stop_px") else "—"] for r in rows], right={2, 3, 4}, stretch=1)
        for i, r in enumerate(rows):
            t.setCellWidget(i, 0, chip_cell(provider_chip(names.get(r["provider_id"], r["provider_id"]),
                                                          r["provider_id"])))
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        t.setColumnWidth(0, 140)
        return t

    @staticmethod
    def _following(rows: list[dict]) -> QWidget:
        card = Card()
        if not rows:
            card.add_row("No providers yet", "Turn one on in Providers.")
        for p in rows:
            card.add_row(p["name"], "Live account" if p.get("account") == "live" else "Paper account",
                         tag("Auto", "good") if p.get("auto_buy") else tag("Alerts only", "neutral"))
        return card

    # --- avisos y botones ---------------------------------------------------------------------------
    def add_alert(self, d: dict):
        self.alerts.insert(0, {**d, "at": datetime.now().strftime("%H:%M")})
        del self.alerts[20:]
        self.refresh()

    def _alerts(self) -> QWidget:
        card = Card()
        for a in self.alerts[:6]:
            card.add_row(f"{a['at']} · {a.get('provider') or 'Signa'}", a["text"])
        clear = button("Clear", "quiet")
        clear.clicked.connect(lambda: (self.alerts.clear(), self.refresh()))
        card.add_row("", None, clear)
        return card

    def _toggle_pause(self):
        self.pause_requested.emit(not self.paused)

    def _close_all(self):
        box = QMessageBox(self)
        box.setWindowTitle("Close all")
        box.setText("Sell ALL your open Signa trades at market now?")
        box.setInformativeText("Every account and provider. It's what you'd do by hand in each trade's drawer. "
                               "Positions of other programs aren't touched.")
        yes = box.addButton("Sell everything", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.close_all_requested.emit()

    def _connection(self) -> QWidget:
        text, kind = STATUS.get(self.status, STATUS["connecting"])
        card = Card()
        card.add_row("Signa server", "Receives your providers' calls", tag(text, kind, dot=kind == "good"))
        return card
