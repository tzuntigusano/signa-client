"""Stats: cómo van tus proveedores con Signa (acierto, ganancia y pérdida media, profit factor, esperanza, P&L, peor
racha), por hora de entrada y por motivo de salida. Lo de Signa lo calcula el servidor con todo tu historial; si en
este PC está tu copiador, la app calcula lo mismo con sus bases (solo lectura) para comparar."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QWidget

from .. import local
from ..widgets import Card, Segmented, chip_cell, columns, hbox, kpi, label, provider_chip, section, table, vbox, widget
from .base import Page
from .trades import money


def pct(v) -> str:
    return "—" if v is None else f"{v:.0%}"


def num(v, fmt="{:+,.2f}") -> str:
    if v is None:
        return "—"
    if v == float("inf"):
        return "∞"
    return fmt.format(v)


def tone(v) -> str | None:
    return None if v is None else "good" if v > 0 else "bad" if v < 0 else "mu"


class StatsScreen(Page):
    stats_requested = Signal(str)  # periodo -> la ventana lo pide al servidor

    def __init__(self):
        self.seg = Segmented([("7d", "Last 7 days"), ("30d", "Last 30 days"), ("all", "All")], "30d")
        super().__init__("Stats", right=self.seg)
        self.period = "30d"
        self.names: dict[str, str] = {"mine": "My calls"}
        self.report: dict | None = None
        self.seg.changed.connect(self._set_period)
        self.host = QWidget()
        self.host.setLayout(vbox(spacing=18))
        self.add(self.host)
        self.finish()
        self.paint()

    def set_providers(self, providers: list[dict]):
        self.names = {"mine": "My calls", **{p["id"]: p["name"] for p in providers}}
        self.paint()

    def showEvent(self, e):  # al entrar en la pantalla, números frescos
        super().showEvent(e)
        self.stats_requested.emit(self.period)

    def _set_period(self, key: str):
        self.period = key
        self.report = None
        self.paint()
        self.stats_requested.emit(key)

    def set_report(self, report: dict | None, error: str = ""):
        if report and report.get("period") != self.period:
            return
        self.report = report
        self.error = error
        self.paint()

    # --- pintar -----------------------------------------------------------------------------------
    def paint(self):
        lay = self.host.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        r = self.report
        if r is None:
            lay.addWidget(label(getattr(self, "error", "") or "Loading your stats…", "mu"))
            return
        t = r["total"]
        if not t["bought"] and not t["skipped"]:
            card = Card(padding=20)
            card.add(label("No trades in this period yet", "h2"))
            card.add(label("Stats show up once Signa trades for you.", "mu"))
            lay.addWidget(card)
            return
        strip = QFrame()
        strip.setLayout(hbox(kpi("P&L", money(t["pnl"]), f"{t['bought']} trades · {t['open']} open", tone(t["pnl"])),
                             kpi("Win rate", pct(t["win_rate"]), f"of {t['closed']} closed"),
                             kpi("Profit factor", num(t["profit_factor"], "{:.2f}"), "Wins ÷ losses"),
                             kpi("Expectancy", num(t["expectancy"]), "Average per closed trade", tone(t["expectancy"])),
                             kpi("Worst drawdown", num(t["max_drawdown"]), "Biggest drop of your P&L",
                                 "bad" if t["max_drawdown"] else None), spacing=12))
        lay.addWidget(strip)
        lay.addWidget(section("By provider", self._providers(r)))
        lay.addWidget(widget(columns(section("By entry hour (ET)", self._groups(r["by_hour"], "Hour")),
                                     section("By exit", self._groups(r["by_exit"], "Exit")))))

    def _providers(self, r: dict) -> QWidget:
        copier = local.copier_stats(self.period)  # None si en este PC no está el copiador
        rows, data, tones = list(r["providers"].items()), [], {}
        for i, (pid, s) in enumerate(rows):
            line = ["", str(s["bought"]), pct(s["win_rate"]), num(s["avg_win"]), num(s["avg_loss"]),
                    num(s["profit_factor"], "{:.2f}"), num(s["expectancy"]), money(s["pnl"]), num(s["max_drawdown"])]
            tones[(i, 7)] = tone(s["pnl"])
            if copier is not None:
                c = copier.get(pid)
                line += [money(c["pnl"]) if c else "—", pct(c["win_rate"]) if c else "—"]
                tones[(i, 9)] = tone(c["pnl"]) if c else None
            data.append(line)
        heads = ["Provider", "Trades", "Win rate", "Avg win", "Avg loss", "Profit f.", "Expectancy", "P&L", "Max DD"]
        if copier is not None:
            heads += ["Copier P&L", "Copier win"]
        tbl = table(heads, data, right=set(range(1, len(heads))), tones=tones, stretch=0)
        for i, (pid, _s) in enumerate(rows):
            tbl.setCellWidget(i, 0, chip_cell(provider_chip(self.names.get(pid, pid), pid)))
        tbl.setTextElideMode(Qt.ElideRight)
        box = widget(vbox(tbl, *([label("Copier: the same numbers from your local trade-copier on this PC, same "
                                         "period.", "small")] if copier is not None else []), spacing=6))
        return box

    @staticmethod
    def _groups(groups: dict, name: str) -> QWidget:
        rows = [(k, s) for k, s in groups.items() if s["bought"]]
        if not rows:
            return label("Nothing yet", "mu")
        data = [[k, str(s["closed"]), pct(s["win_rate"]), money(s["pnl"])] for k, s in rows]
        tones = {(i, 3): tone(s["pnl"]) for i, (_k, s) in enumerate(rows)}
        return table([name, "Closed", "Win rate", "P&L"], data, right={1, 2, 3}, tones=tones, stretch=0)

