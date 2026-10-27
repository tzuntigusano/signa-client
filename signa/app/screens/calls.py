"""Calls: cada mensaje de tus proveedores en directo, cómo lo entendió el servidor y (cuando haya ejecutor) qué
se hizo con él."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLineEdit, QTableWidget, QTableWidgetItem

from .. import theme
from ..widgets import Segmented, chip_cell, hbox, label, provider_chip, tag
from .base import Page

# tipo del mensaje -> (chip, color de estado). Los proveedores llevan OTROS colores (theme.PROVIDER_PALETTE)
KIND = {"entry": ("Buy", "good"), "update": ("Price", "neutral"), "warning": ("Warning", "warn"),
        "exit": ("Exit", "accent"), "stop": ("Stop", "accent"), "manage": ("Manage", "accent"),
        "repost": ("Repeat", "neutral"), "comment": ("Comment", "neutral"), "other": ("Chat", "neutral")}
FILTER = {"all": None, "entries": {"entry"}, "alerts": {"warning", "exit", "stop", "manage"},
          "other": {"update", "comment", "other", "repost"}}
MAX_ROWS = 300


def kind_chip(s: dict):
    name, color = KIND.get(s["kind"], KIND["other"])
    return tag(name, color)


def contract_text(s: dict) -> str:
    """Lo que dice la call SIN el verbo (eso va en el chip del tipo): «SPY 768C 10/27 · lmt 0.44»."""
    p = s.get("parsed") or {}
    c = p.get("contract")
    if not c:
        return " ".join(s["text"].split())[:60]
    what = f"{c[0]} {c[1]:g}{c[2]}"
    if s["kind"] == "entry":
        exp = f" {c[3][4:6]}/{c[3][6:8]}" if c[3] else ""
        return f"{what}{exp} · " + (f"lmt {p['limit']:.2f}" if p.get("limit") is not None else "no price yet")
    if p.get("limit") is not None:
        return f"{what} · {p['limit']:.2f}"
    return what


def read_as(s: dict) -> str:
    p = s.get("parsed") or {}
    c = p.get("contract")
    what = f"{c[0]} {c[1]:g}{c[2]}" if c else ""
    if s["kind"] == "entry":
        exp = f" {c[3][4:6]}/{c[3][6:8]}" if c and c[3] else ""
        px = f" · lmt {p['limit']:.2f}" if p.get("limit") is not None else " · no price yet"
        return f"Buy {what}{exp}{px}"
    if s["kind"] == "update" and p.get("limit") is not None:
        return f"{what} at {p['limit']:.2f}"
    if s["kind"] in ("exit", "stop"):
        return f"{s['kind'].title()} {what}".strip()
    if s["kind"] == "warning":
        return f"Warning {what}".strip()
    return "—"


def clock(ts: str | None) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return ""


class CallsScreen(Page):
    def __init__(self):
        search = QLineEdit()
        search.setPlaceholderText("SPY 765")
        search.setFixedWidth(220)
        search.textChanged.connect(lambda _t: self._apply())
        self.search = search
        self.count = label("", "mu")
        super().__init__("Calls", None, search)
        self.filter = "all"
        self.names: dict[str, str] = {}
        self.rows: list[dict] = []

        seg = Segmented([("all", "All"), ("entries", "Entries"), ("alerts", "Alerts"), ("other", "Prices and chat")],
                        "all")
        seg.changed.connect(self._set_filter)
        self.add(hbox(seg, None, self.count))

        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["Time", "Provider", "Message", "Read as", "Type"])
        t.verticalHeader().hide()
        t.setShowGrid(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setFocusPolicy(Qt.NoFocus)
        t.setWordWrap(False)
        t.verticalHeader().setDefaultSectionSize(36)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        hh = t.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        for col, w in ((0, 92), (1, 140), (3, 270), (4, 110)):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            t.setColumnWidth(col, w)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        t.setTextElideMode(Qt.ElideRight)
        t.setMinimumHeight(420)
        self.tbl = t
        self.add(t)
        self.empty = label("No calls yet today. They show up here the moment your providers post them.", "mu")
        self.add(self.empty)
        self.add(label("Every call is stored with its bid and ask over time, so skipped calls can be reviewed later.",
                       "small"))
        self.finish()
        self._apply()

    # --- datos ------------------------------------------------------------------------------------
    def set_providers(self, providers: list[dict]):
        self.names = {p["id"]: p["name"] for p in providers}

    def set_signals(self, signals: list[dict]):
        """Las recientes al conectar (vienen de la más nueva a la más vieja)."""
        self.rows = list(signals)[:MAX_ROWS]
        self._apply()

    def add_signal(self, s: dict):
        self.rows.insert(0, s)
        del self.rows[MAX_ROWS:]
        self._apply()

    # --- pintado ----------------------------------------------------------------------------------
    def _set_filter(self, key: str):
        self.filter = key
        self._apply()

    def _visible(self) -> list[dict]:
        kinds, q = FILTER[self.filter], self.search.text().strip().lower()
        return [s for s in self.rows if (kinds is None or s["kind"] in kinds) and (not q or q in s["text"].lower())]

    def _apply(self):
        rows = self._visible()
        t = self.tbl
        t.setRowCount(len(rows))
        mu = theme.current["mu"]
        for i, s in enumerate(rows):
            for col, text in ((0, clock(s.get("ts"))), (2, " ".join(s["text"].split())), (3, read_as(s))):
                it = QTableWidgetItem(text)
                if col == 3 and text == "—":
                    it.setForeground(QColor(mu))
                t.setItem(i, col, it)
            t.setItem(i, 1, QTableWidgetItem(""))
            t.setCellWidget(i, 1, chip_cell(provider_chip(self.names.get(s["provider"], s["provider"]),
                                                          s["provider"])))
            t.setCellWidget(i, 4, chip_cell(kind_chip(s)))
        entries = sum(s["kind"] == "entry" for s in self.rows)
        alerts = sum(s["kind"] in FILTER["alerts"] for s in self.rows)
        self.count.setText(f"{len(self.rows)} messages · {entries} entries · {alerts} alerts")
        self.empty.setVisible(not self.rows)
