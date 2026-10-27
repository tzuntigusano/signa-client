"""Admin (solo cuentas admin): usuarios e invitaciones, qué proveedores tiene cada uno, proveedores solo paper, el
motor de cada proveedor (SL / TP1 / TP2 / persecución, para todos sus usuarios) y los filtros de mensajes. Todo lo
aplica y valida el servidor (admin_panel.py); esto solo lo enseña y lo manda."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QMessageBox, QWidget

from ..widgets import Card, Toggle, button, columns, hbox, label, section, tag, vbox, widget
from .base import Page

KINDS = ["links", "word", "regex", "mentions", "emoji"]


def _num(v) -> str:
    return "" if v is None else f"{float(v):g}"


class AdminScreen(Page):
    admin_request = Signal(str, dict)  # (acción, datos) -> la ventana lo manda al servidor

    def __init__(self):
        self.reload_btn = button("Reload")
        super().__init__("Admin", "Only you see this", right=self.reload_btn)
        self.reload_btn.clicked.connect(lambda: self.admin_request.emit("admin_state", {}))
        self.state: dict | None = None
        self.msg = label("", "small")
        self.host = QWidget()
        self.host.setLayout(vbox(spacing=18))
        self.add(self.msg)
        self.add(self.host)
        self.finish()
        self.paint()

    def showEvent(self, e):
        super().showEvent(e)
        self.admin_request.emit("admin_state", {})

    def set_state(self, state: dict):
        self.state = state
        self.paint()

    def note(self, text: str, ok: bool = True):
        self.msg.setText(("✓  " if ok else "✕  ") + text)

    def _send(self, kind: str, **data):
        self.msg.setText("Saving…")
        self.admin_request.emit(kind, data)

    # --- pintar -----------------------------------------------------------------------------------
    def paint(self):
        lay = self.host.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        if self.state is None:
            lay.addWidget(label("Loading…", "mu"))
            return
        provs = self.state["providers"]
        lay.addWidget(section("Users", self._users(self.state["users"], provs)))
        lay.addWidget(widget(columns(section("Providers", self._providers(provs)),
                                     section("Message filters", self._filters(self.state["filters"], provs)))))

    def _users(self, users: list[dict], provs: list[dict]) -> QWidget:
        card = Card()
        for u in users:
            boxes = []
            for p in provs:
                cb = QCheckBox(p["name"])
                cb.setChecked(p["id"] in u["providers"])
                cb.toggled.connect(lambda on, uid=u["id"], pid=p["id"]:
                                   self._send("admin_enable" if on else "admin_disable", user_id=uid, provider=pid))
                boxes.append(cb)
            role = tag("Admin", "accent") if u.get("role") == "admin" else tag(u.get("status") or "active", "neutral")
            seen = (u.get("last_seen") or "never")[:16].replace("T", " ")
            card.add_row(u["name"], f"Last seen {seen}", widget(hbox(role, *boxes, spacing=12)))
        name = QLineEdit()
        name.setPlaceholderText("Name of the new user")
        invite = button("Create invite", "primary")

        def make():
            if name.text().strip():
                self._send("admin_invite", name=name.text().strip())
        invite.clicked.connect(make)
        name.returnPressed.connect(make)
        card.add_row("Invite someone", "Creates the user and a one-time invite key", widget(hbox(name, invite)))
        return card

    def _providers(self, provs: list[dict]) -> QWidget:
        card = Card(padding=16)
        card.add(label("Engine: empty = the copier's value (grey). Applies to every user of the provider.", "small",
                       wrap=True) if all(p.get("has_engine_column") for p in provs) else
                 label("Engine settings need migration 004 on Supabase (server/sql/004_engine_config.sql).",
                       "small", "warn", wrap=True))
        for p in provs:
            d = p.get("defaults") or {}
            po = Toggle(p.get("paper_only"), "Paper only")
            po.toggled.connect(lambda on, pid=p["id"]: self._send("admin_paper_only", provider=pid, on=on))
            fields, cols = {}, []
            for key, name in (("sl", "SL %"), ("tp1", "TP1 %"), ("tp2", "TP2 %"), ("chase", "Chase %")):
                e = QLineEdit(_num((p.get("engine") or {}).get(key)))
                e.setPlaceholderText(_num(d.get(key)) or ("rule" if key == "chase" else "none"))
                e.setFixedWidth(70)
                e.setProperty("mono", "true")
                e.setEnabled(bool(p.get("has_engine_column")))
                fields[key] = e
                cols.append(widget(vbox(label(name, "small"), e, spacing=3)))
            save = button("Save")
            save.setEnabled(bool(p.get("has_engine_column")))
            save.clicked.connect(lambda _=False, pid=p["id"], f=fields:
                                 self._send("admin_engine", provider=pid,
                                            engine={k: e.text().strip().rstrip("%") for k, e in f.items()}))
            card.add(widget(vbox(hbox(label(p["name"], "name"), None, label("Paper only", "small"), po),
                                 hbox(*cols, None, save, spacing=8), spacing=8)))
        return card

    def _filters(self, filters: list[dict], provs: list[dict]) -> QWidget:
        card = Card(padding=14)
        rows = []
        box = vbox(spacing=6)

        def add_row(f: dict | None = None):
            f = f or {"kind": "word"}
            kind = QComboBox()
            kind.addItems(KINDS)
            kind.setCurrentText(f.get("kind", "word"))
            value = QLineEdit(str(f.get("value") or ""))
            value.setPlaceholderText("word or pattern")
            prov = QComboBox()
            prov.addItem("All providers", None)
            for p in provs:
                prov.addItem(p["name"], p["id"])
            prov.setCurrentIndex(max(0, prov.findData(f.get("provider"))))
            inside = QCheckBox("inside words")
            inside.setChecked(bool(f.get("inside")))
            rm = button("✕", "quiet")
            line = widget(hbox(kind, value, prov, inside, rm, spacing=6))
            row = {"kind": kind, "value": value, "prov": prov, "inside": inside, "w": line}
            rm.clicked.connect(lambda: (rows.remove(row), line.hide(), line.deleteLater()))
            rows.append(row)
            box.addWidget(line)

        for f in filters:
            add_row(f)
        add = button("Add filter")
        add.clicked.connect(lambda: add_row())
        save = button("Save filters", "primary")
        save.clicked.connect(lambda: self._send("admin_filters", filters=[
            {"kind": r["kind"].currentText(), "value": r["value"].text().strip(), "provider": r["prov"].currentData(),
             "inside": r["inside"].isChecked()} for r in rows]))
        card.add(label("Hidden from the app's messages (the database keeps the originals). Case doesn't matter.",
                       "small", wrap=True))
        card.add(widget(box))
        card.add(widget(hbox(add, None, save)))
        return card

    def show_invite(self, name: str, code: str):
        box = QMessageBox(self)
        box.setWindowTitle("Invite created")
        box.setText(f"Invite key for {name} (shown only once):")
        box.setInformativeText(code)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
