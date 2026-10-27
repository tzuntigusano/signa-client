"""Providers: los proveedores que el admin te habilitó (los demás ni llegan desde el servidor) y, por cada uno,
seguirlo o no, la cuenta (Paper/Live), los 4 interruptores, el tamaño y el riesgo. «Save changes» solo se activa
si hay cambios y lo guarda en el servidor (te sigue a cualquier ordenador); al guardarse sale «Changes saved»."""
import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QLineEdit, QMessageBox, QWidget

from ..widgets import Card, Segmented, Toggle, button, columns, hbox, label, section, tag, vbox, widget
from .base import Page

TOGGLES = [
    ("auto_buy", "Auto buy", "Buys as soon as the call arrives. Off: you get a card to buy."),
    ("auto_protect", "Auto exit on protection", "The provider's warnings sell or raise the stop. Off: alert card."),
    ("auto_rules", "Auto exit on rules", "Stop ladder and trailing act alone. Off: they only alert you."),
    ("all_msgs", "Show all messages", "Forward the provider's chat too, not only calls and fills."),
]
SIZE_LABEL = {"contracts": ("Contracts", "contracts", ""), "amount": ("Amount per trade", "amount", "$"),
              "pct": ("Account per trade", "pct", "%")}
SAVED_FIELDS = ("followed", "account", "auto_buy", "auto_protect", "auto_rules", "all_msgs", "size_by", "contracts",
                "amount", "pct", "stop_pct", "chase_pct", "hours_from", "hours_to")
SAVED_MSG_MS = 4000


def _field(value: str, width: int = 110) -> QLineEdit:
    e = QLineEdit(value)
    e.setProperty("mono", "true")
    e.setAlignment(Qt.AlignRight)
    e.setFixedWidth(width)
    return e


def _num(text: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def _fmt(v) -> str:
    return "" if v is None else f"{float(v):g}"


def snapshot(p: dict) -> dict:
    """Los ajustes que se guardan, normalizados para poder comparar (2 == 2.0, «09:30:00» == «09:30»)."""
    out = {}
    for k in SAVED_FIELDS:
        v = p.get(k)
        if k in ("contracts", "amount", "pct", "stop_pct", "chase_pct") and v is not None:
            v = float(v)
        elif k in ("hours_from", "hours_to") and v:
            v = str(v)[:5]
        out[k] = v
    return out


def size_text(p: dict) -> str:
    v = p.get(SIZE_LABEL[p["size_by"]][1])
    if p["size_by"] == "contracts":
        n = int(v or 1)
        return f"{n} contract{'s' if n != 1 else ''}"
    return f"${_fmt(v)} per trade" if p["size_by"] == "amount" else f"{_fmt(v)}% per trade"


class ProviderItem(QFrame):
    """Un proveedor de la lista de la izquierda; clic = verlo a la derecha."""

    def __init__(self, p: dict, selected: bool, dirty: bool, on_click):
        super().__init__()
        self.setProperty("card", "true" if selected else "false")
        self.setCursor(Qt.PointingHandCursor)
        self._on_click = on_click
        if p["followed"]:
            sub = f"{size_text(p)} · {'Live' if p['account'] == 'live' else 'Paper'}"
            right = tag("Active", dot=True)
        else:
            sub, right = "Enabled · not followed", tag("Off", "neutral")
        if dirty:
            sub += " · unsaved"
        text = vbox(label(p["name"], "name"), label(sub, "small"), spacing=2)
        self.setLayout(hbox(text, None, right, margins=(14, 12, 14, 12)))

    def mousePressEvent(self, _e):
        self._on_click()


class ProvidersScreen(Page):
    save = Signal(str, dict)  # (proveedor, campos) -> la ventana lo manda al servidor

    def __init__(self):
        super().__init__("Providers", None, label("Your admin enables which providers you can follow", "mu"))
        self.body.setContentsMargins(0, 0, 0, 0)
        self.providers: list[dict] = []
        self.stored: dict[str, dict] = {}  # lo guardado en el servidor, por proveedor (para saber si hay cambios)
        self.sel: str | None = None
        self.prov: dict | None = None
        self.saving = False
        self.msg_timer = QTimer(self)
        self.msg_timer.setSingleShot(True)
        self.msg_timer.timeout.connect(lambda: self._update_dirty())
        self.host = QWidget()
        self.host.setLayout(hbox())
        self.add(self.host)
        self._render()

    # --- datos desde el servidor -----------------------------------------------------------------
    def set_providers(self, providers: list[dict]):
        self.providers = [dict(p) for p in providers]
        self.stored = {p["id"]: snapshot(p) for p in providers}
        if not any(p["id"] == self.sel for p in self.providers):
            self.sel = self.providers[0]["id"] if self.providers else None
        self._render()

    def saved(self, res: dict):
        self.saving = False
        if res.get("ok"):
            p = dict(res["provider"])
            self.providers = [p if x["id"] == p["id"] else x for x in self.providers]
            self.stored[p["id"]] = snapshot(p)
            self._render()
            self._message("✓  Changes saved", "good")
        else:
            self._update_dirty()
            self._message(res.get("error") or "Couldn't save. Try again.", "bad")

    # --- montaje ---------------------------------------------------------------------------------
    def _render(self):
        lay = self.host.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.hide()  # que no se vea ni un instante debajo de la nueva
                w.deleteLater()
        self.prov = next((p for p in self.providers if p["id"] == self.sel), None)
        if self.prov is None:
            msg = "Connecting to the Signa server…" if not self.providers and self.sel is None else \
                "No providers enabled for your account yet. Ask your admin."
            lay.addWidget(widget(vbox(label(msg, "mu"), None, margins=(28, 24, 28, 24))))
            return
        lay.addWidget(self._listing())
        lay.addWidget(self._detail(), 1)
        self._update_dirty()

    def _listing(self) -> QWidget:
        listing = QFrame()
        listing.setFixedWidth(250)
        lay = vbox(spacing=4, margins=(12, 16, 12, 16))
        for p in self.providers:
            dirty = snapshot(p) != self.stored.get(p["id"])
            lay.addWidget(ProviderItem(p, p["id"] == self.sel, dirty, lambda pid=p["id"]: self._select(pid)))
        lay.addStretch(1)
        listing.setLayout(lay)
        return listing

    def _detail(self) -> QWidget:
        detail = vbox(self._top(), self._bar(), spacing=12, margins=(24, 20, 24, 20))
        right = section("Position size and risk", self._sizing())
        right.setFixedWidth(340)
        detail.addLayout(columns(section("Automation", self._automation()), right, spacing=16))
        detail.addWidget(label("Settings are saved on the Signa server, so they follow you to any computer.", "small"))
        detail.addStretch(1)
        return widget(detail)

    def _top(self) -> QWidget:
        p = self.prov
        bot = p.get("telegram_bot")
        sub = " · ".join(x for x in (f"Telegram bot {bot}" if bot else "No Telegram bot yet", p.get("description"))
                         if x)
        title = vbox(label(p["name"], "h2"), label(sub, "mu"), spacing=2)
        follow = Toggle(p["followed"], "Follow this provider")
        follow.toggled.connect(self._set_follow)
        state = label("Following" if p["followed"] else "Not following", "name" if p["followed"] else "mu")
        self.save_btn = button("Save changes", "primary")
        self.save_btn.clicked.connect(self._save)
        return widget(hbox(title, None, state, follow, self.save_btn, spacing=12))

    def _bar(self) -> QWidget:
        """Línea bajo la cabecera: «Unsaved changes · Discard», «Saving…», «✓ Changes saved» o un error."""
        self.status_lb = label("", "small")
        self.revert_btn = button("Discard changes", "link")
        self.revert_btn.clicked.connect(self._discard)
        bar = widget(hbox(self.status_lb, self.revert_btn, None, spacing=10))
        bar.setFixedHeight(28)
        return bar

    def _automation(self) -> QWidget:
        card = Card()
        if self.prov.get("paper_only"):  # decisión del admin: el servidor no deja Live para este proveedor
            card.add_row("Trade on", "This provider can only trade on your paper account (set by the admin).",
                         tag("Paper only", "neutral"))
        else:
            acct = Segmented([("paper", "Paper"), ("live", "Live")], self.prov["account"])
            acct.changed.connect(self._set_account)
            card.add_row("Trade on", "Paper is simulated money. Live sends real orders.", acct)
        for key, title, desc in TOGGLES:
            t = Toggle(self.prov[key], title)
            t.toggled.connect(lambda on, k=key: self._set(k, on))
            card.add_row(title, desc, t)
        return card

    def _sizing(self) -> QWidget:
        p = self.prov
        card = Card()
        seg = Segmented([("contracts", "Contracts"), ("amount", "Amount"), ("pct", "% of account")], p["size_by"])
        card.add_row("Size by", None, seg, vertical=True)
        self.size_field = _field("")
        self.size_row = card.add_row("", None, self.size_field)
        seg.changed.connect(self._set_size_by)
        self._show_size()
        # vacíos = lo del proveedor (su motor); lo que escribas manda sobre eso
        d = p.get("defaults") or {}
        self.stop_field = _field(f"{_fmt(p['stop_pct'])} %" if p.get("stop_pct") else "")
        self.stop_field.setPlaceholderText(f"{_fmt(d['sl'])} % (provider)" if d.get("sl") else "Provider's")
        self.chase_field = _field(f"{_fmt(p['chase_pct'])} %" if p.get("chase_pct") is not None else "")
        self.chase_field.setPlaceholderText(f"{_fmt(d['chase'])} % (provider)" if d.get("chase") is not None
                                            else "Provider's rule")
        card.add_row("Initial stop", "Below your entry · empty = the provider's", self.stop_field)
        card.add_row("Max chase", "Skip if the ask is this much higher · empty = the provider's", self.chase_field)
        self.from_field = _field(str(p["hours_from"])[:5], 64)
        self.to_field = _field(str(p["hours_to"])[:5], 64)
        card.add_row("Trading hours (ET)", None,
                     widget(hbox(self.from_field, label("–", "mu"), self.to_field, spacing=6)))
        for f in (self.size_field, self.stop_field, self.chase_field, self.from_field, self.to_field):
            f.textEdited.connect(lambda _t: self._read_fields())
        return card

    # --- cambios ---------------------------------------------------------------------------------
    def has_unsaved(self) -> bool:
        """¿Algún proveedor con cambios sin guardar? (entonces una sincronización no repinta la pantalla)."""
        return any(snapshot(p) != self.stored.get(p["id"]) for p in self.providers)

    def is_dirty(self) -> bool:
        return self.prov is not None and snapshot(self.prov) != self.stored.get(self.prov["id"])

    def _update_dirty(self):
        if self.prov is None or not hasattr(self, "save_btn"):
            return
        dirty = self.is_dirty()
        self.save_btn.setEnabled(dirty and not self.saving)
        self.revert_btn.setVisible(dirty and not self.saving)
        if not self.msg_timer.isActive():
            self._status("Saving…" if self.saving else "Unsaved changes" if dirty else "", "warn")

    def _message(self, text: str, tone: str):
        """Mensaje que se queda unos segundos (confirmación o error) y luego vuelve el estado normal."""
        self._status(text, tone)
        self.msg_timer.start(SAVED_MSG_MS)

    def _status(self, text: str, tone: str):
        self.status_lb.setText(text)
        self.status_lb.setProperty("tone", tone)
        self.status_lb.style().polish(self.status_lb)

    def _read_fields(self):
        """Lo escrito en las casillas pasa al proveedor (sin validar: eso lo hace el servidor al guardar)."""
        p = self.prov
        size = _num(self.size_field.text())
        field = SIZE_LABEL[p["size_by"]][1]
        p[field] = int(size) if field == "contracts" and size else size
        p["stop_pct"] = _num(self.stop_field.text())
        p["chase_pct"] = _num(self.chase_field.text())
        p["hours_from"], p["hours_to"] = self.from_field.text().strip(), self.to_field.text().strip()
        self.msg_timer.stop()
        self._update_dirty()

    def _select(self, pid: str):
        if pid != self.sel:
            self.sel = pid
            self.msg_timer.stop()
            self._render()

    def _set_follow(self, on: bool):
        self.prov["followed"] = on
        self.msg_timer.stop()
        self._render()

    def _set_account(self, key: str):
        """Cada proveedor elige su cuenta. Pasar a Live pide confirmación; si no, se queda en Paper."""
        if key == "live" and not self._confirm_live():
            self._render()  # deshace la selección visual
            return
        self.prov["account"] = key
        self.msg_timer.stop()
        self._update_dirty()

    def _confirm_live(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Trade with real money?")
        box.setText(f"{self.prov['name']} will place real orders in your live IBKR account.")
        box.setInformativeText("You can switch back to Paper at any time.")
        live = box.addButton("Use live account", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is live

    def _set(self, key: str, on: bool):
        self.prov[key] = on
        self.msg_timer.stop()
        self._update_dirty()

    def _set_size_by(self, key: str):
        self.prov["size_by"] = key
        self._show_size()
        self.msg_timer.stop()
        self._update_dirty()

    def _show_size(self):
        title, field, unit = SIZE_LABEL[self.prov["size_by"]]
        value = self.prov.get(field)
        self.size_row.title.setText(title)
        v = _fmt(value) or ("1" if field == "contracts" else "")
        self.size_field.setText(f"${v}" if unit == "$" and v else f"{v} %" if unit == "%" and v else v)

    def _discard(self):
        """Vuelve a lo guardado en el servidor."""
        stored = self.stored.get(self.prov["id"], {})
        self.prov.update({k: v for k, v in stored.items()})
        self.msg_timer.stop()
        self._render()

    def fields(self) -> dict:
        """Lo que se manda al guardar (el servidor vuelve a validarlo todo)."""
        return {k: v for k, v in snapshot(self.prov).items() if not (k in ("amount", "pct") and v is None)}

    def _save(self):
        if self.prov is None or not self.is_dirty():
            return
        self.saving = True
        self.msg_timer.stop()
        self._update_dirty()
        self.save.emit(self.prov["id"], self.fields())
