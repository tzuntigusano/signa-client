"""Settings: apariencia, arranque con Windows, bandeja, versión, condiciones aceptadas, exportar / borrar la cuenta y
diagnóstico. TODO real: nada que solo «parezca» un ajuste."""
from datetime import datetime

from PySide6.QtCore import QSettings, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QMessageBox, QTextBrowser

from ... import __version__
from ...core import changelog
from ...core.logs import HOME
from .. import autostart, updater
from .. import client as net
from ..widgets import Card, Segmented, Toggle, button, columns, hbox, label, section, tag, vbox, widget
from .base import Page
from .telegram import Job

TERMS = {"data": "Data terms", "use": "Terms of use"}
LOG_TAIL = 256 * 1024  # de cada log, lo último
SHOW_VERSIONS = 5  # versiones con su lista de cambios en el aviso de actualización


def version_tuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in (v or "0").split("."))


def whats_new(changes: list[dict] | None, latest: str | None) -> str:
    """«What's new» del aviso de actualización (HTML): lo de TODAS las versiones entre la tuya y la nueva (las
    SHOW_VERSIONS más nuevas, para que el aviso no crezca sin fin). Sin notas, nada."""
    got = changelog.between(changes, __version__, latest or "0")
    if not got:
        return ""
    more = len(got) - SHOW_VERSIONS
    return ("<p><b>What's new</b></p>" + changelog.as_html(got[:SHOW_VERSIONS])
            + (f"<p>…and {more} earlier version{'s' if more > 1 else ''}.</p>" if more > 0 else ""))


def logs_blob() -> bytes:
    """El final de cada log de Signa en este PC, en un solo texto."""
    parts = []
    for f in sorted(HOME.glob("*.log")):
        data = f.read_bytes()[-LOG_TAIL:]
        parts.append(f"===== {f.name} ({f.stat().st_size // 1024} KB, last {len(data) // 1024} KB) =====\n".encode()
                     + data)
    return b"\n\n".join(parts) or b"(no logs)"


class TermsDialog(QDialog):
    def __init__(self, parent, title: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("Page")
        self.resize(640, 560)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(text)
        close = button("Close", "primary")
        close.clicked.connect(self.accept)
        self.setLayout(vbox(view, hbox(None, close), spacing=12, margins=(16, 16, 16, 16)))


class DeleteDialog(QDialog):
    """Borrar la cuenta: hay que escribir DELETE."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Delete account")
        self.setObjectName("Page")
        self.setMinimumWidth(460)
        self.word = QLineEdit()
        self.word.setPlaceholderText("Type DELETE")
        self.local = QCheckBox("Also remove this computer's Telegram bots and IBKR logins")
        self.local.setChecked(True)
        cancel, self.ok = button("Cancel"), button("Delete my account", "danger")
        self.ok.setEnabled(False)
        self.word.textChanged.connect(lambda t: self.ok.setEnabled(t.strip() == "DELETE"))
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        card = Card(padding=20)
        card.add(vbox(label("This deletes your Signa account for good: your settings, providers and trade history on "
                            "the Signa server. Your positions and orders at IBKR are not touched. To use Signa "
                            "again you'd need a new invite.", "small", wrap=True),
                      self.word, self.local, spacing=10))
        self.setLayout(vbox(card, hbox(None, cancel, self.ok), spacing=14, margins=(18, 18, 18, 18)))


class SettingsScreen(Page):
    theme_changed = Signal(str)
    check_updates = Signal()
    export_requested = Signal()
    delete_requested = Signal(bool)  # ¿borrar también lo de este PC?
    risk_save = Signal(dict)  # límites de riesgo -> servidor
    update_ready = Signal()  # el instalador ya está lanzado: la app debe cerrarse
    new_key_requested = Signal()  # «Use a new invite key»: la ventana olvida la clave y enseña Welcome
    pair_requested = Signal()  # «Link my phone»: la ventana pide el código al servidor

    def __init__(self, theme_mode: str):
        super().__init__("Settings")
        self.prefs = QSettings("Signa", "Signa")
        self.consents: list[dict] = []
        self.latest: str | None = None
        self.changes: list[dict] = []  # lo nuevo de cada versión publicada (lo pone la ventana)
        self.role = "user"
        self.jobs: list[Job] = []

        appearance = Segmented([("system", "System"), ("light", "Light"), ("dark", "Dark")], theme_mode)
        appearance.changed.connect(self.theme_changed)
        general = Card()
        general.add_row("Appearance", None, appearance)
        self.boot = Toggle(autostart.is_enabled(), "Start with Windows")
        self.boot.toggled.connect(self._set_boot)
        general.add_row("Start Signa when you sign in",
                        "Opens Signa when you sign in, and Signa starts the executor and "
                        "your IB Gateways", self.boot)
        tray = Toggle(self.prefs.value("tray", True, type=bool), "Keep running when closed")
        tray.toggled.connect(lambda on: self.prefs.setValue("tray", on))
        general.add_row("Keep running when closed", "Closing the window leaves Signa in the system tray. Trading "
                        "and cards never depend on the window.", tray)

        self.updates = Card()
        self.privacy = Card()
        diag = Card()
        folder, send = button("Open folder"), button("Send to Signa")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(HOME))))
        send.clicked.connect(lambda: self._send_logs(send))
        diag.add_row("Logs", "Signa's logs on this computer. Sending them shares the latest part with the Signa "
                     "admin, for support.", widget(hbox(folder, send)))

        self.risk_card = self._risk_card()
        left = vbox(section("General", general), section("Risk limits", self.risk_card),
                    section("Updates", self.updates), spacing=18)
        phone = Card()
        link = button("Link my phone")
        link.clicked.connect(self.pair_requested)
        phone.add_row("Signa for Android", "See your trades, calls and stats on your phone and trade from it. Your "
                      "IBKR and Telegram bots keep running on this computer.", link)
        right = widget(vbox(section("Data and privacy", self.privacy), section("Phone", phone),
                            section("Diagnostics", diag), spacing=18))
        right.setFixedWidth(430)
        self.add(columns(widget(left), right))
        self.finish()
        self.set_account()

    # --- límites de riesgo (los aplica el servidor antes de CADA compra) ---------------------------------
    RISK_FIELDS = (("max_daily_loss", "Max loss per day ($)", "Stop buying for the day after losing this much"),
                   ("max_positions", "Max open positions", "Across all your providers on the same account"),
                   ("max_contracts", "Max contracts per order", "Bigger orders are not sent"))

    def _risk_card(self) -> Card:
        card = Card()
        self.risk_inputs = {}
        for key, title, desc in self.RISK_FIELDS:
            e = QLineEdit()
            e.setPlaceholderText("No limit")
            e.setFixedWidth(110)
            e.setProperty("mono", "true")
            self.risk_inputs[key] = e
            card.add_row(title, desc, e)
        self.risk_msg = label("", "small")
        save = button("Save limits", "primary")
        save.clicked.connect(self._save_risk)
        card.add_row("", None, widget(hbox(self.risk_msg, save)))
        return card

    def set_risk(self, risk: dict, note: str = ""):
        for key, e in self.risk_inputs.items():
            v = risk.get(key)
            e.setText("" if v is None else f"{v:g}")
        self.risk_msg.setText(note)

    def _save_risk(self):
        fields = {}
        for key, e in self.risk_inputs.items():
            t = e.text().strip().replace(",", ".").lstrip("$")
            if not t:
                fields[key] = None
                continue
            try:
                fields[key] = float(t) if key == "max_daily_loss" else int(float(t))
            except ValueError:
                self.risk_msg.setText(f"✕  {key.replace('_', ' ')}: type a number")
                return
        self.risk_msg.setText("Saving…")
        self.risk_save.emit(fields)

    # --- datos del servidor -----------------------------------------------------------------------
    def set_account(self, consents: list[dict] | None = None, latest: str | None = None, role: str | None = None):
        if consents is not None:
            self.consents = consents
        if latest is not None:
            self.latest = latest
        if role is not None:
            self.role = role
        self._paint_updates()
        self._paint_privacy()

    def _clear(self, card: Card):
        while card.lay.count():
            w = card.lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        card._rows = 0

    def _paint_updates(self, checking: bool = False):
        self._clear(self.updates)
        if checking:
            state = tag("Checking…", "neutral")
        elif self.latest is None:
            state = tag("Unknown until connected", "neutral")
        elif version_tuple(self.latest) > version_tuple(__version__):
            state = tag(f"v{self.latest} available", "warn")
        else:
            state = tag("Up to date", "good")
        check = button("Check now")
        check.clicked.connect(self._check)
        self.updates.add_row("Version", None, widget(hbox(label(__version__, "mono"), state, check)))
        self.install_btn = None
        if self.latest and version_tuple(self.latest) > version_tuple(__version__):
            go = self.install_btn = button(f"Install v{self.latest}", "primary")
            go.clicked.connect(lambda: self._update(go))
            self.updates.add_row("Update", "Signed by Signa and checked before installing. Signa closes for a "
                                 "moment and opens again on the new version.", go)

    def _update(self, btn):
        from ...core.market import market_open
        box = QMessageBox(self)
        box.setWindowTitle("Update Signa")
        box.setText(f"Install Signa v{self.latest} now?")
        box.setInformativeText(whats_new(self.changes, self.latest)
                               + "<p>Signa stops for about a minute: no new trades in that time (your stops and "
                               "take profits stay in IBKR).</p>"
                               + ("<p>The US market is OPEN right now.</p>" if market_open() else ""))
        yes = box.addButton("Install", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        self._install(btn)

    def install_now(self):
        """Desde el aviso de versión nueva (ya ha dicho que sí): lo mismo que el botón Install, sin volver a
        preguntar. El progreso se ve en ese botón."""
        self._paint_updates()
        if self.install_btn is not None and self.install_btn.isEnabled():
            self._install(self.install_btn)

    def _install(self, btn):
        btn.setEnabled(False)
        btn.setText("Downloading…")

        def done(path, err):
            if err:
                btn.setEnabled(True)
                btn.setText(f"Install v{self.latest}")
                QMessageBox.warning(self, "Update Signa", err)
                return
            try:
                updater.install(path)
            except updater.UpdateError as e:
                QMessageBox.warning(self, "Update Signa", str(e))
                btn.setEnabled(True)
                return
            self.update_ready.emit()  # la app se cierra y el instalador la vuelve a abrir
        self._job(updater.download_and_check, then=done)

    def _check(self):
        self._paint_updates(checking=True)
        self.check_updates.emit()

    def _paint_privacy(self):
        self._clear(self.privacy)
        latest: dict[str, dict] = {}
        for c in self.consents:
            if c["kind"] not in latest or c["version"] > latest[c["kind"]]["version"]:
                latest[c["kind"]] = c
        if not latest:
            self.privacy.add_row("Terms you accepted", "Shown once connected to the Signa server.")
        for kind, c in sorted(latest.items()):
            when = c.get("accepted_at")
            try:
                when = datetime.fromisoformat(when).astimezone().strftime("%b %d, %Y") if when else None
            except ValueError:
                pass
            read = button("Read")
            read.clicked.connect(lambda _=False, k=kind, v=c["version"], b=read: self._read(k, v, b))
            self.privacy.add_row(TERMS.get(kind, kind), f"Version {c['version']}" + (f" · accepted {when}"
                                                                                       if when else ""), read)
        export = button("Export my data")
        export.clicked.connect(self.export_requested)
        self.privacy.add_row("Your data", "Everything the Signa server keeps about you, as a JSON file.", export)
        new_key = button("Use a new key…")
        new_key.clicked.connect(self.new_key_requested)
        self.privacy.add_row("Invite key", "Got a new invite key from the admin? Enter it here. Your bots, IBKR "
                             "logins and settings stay.", new_key)
        delete = button("Delete account", "danger")
        delete.clicked.connect(self._delete)
        if self.role == "admin":
            delete.setEnabled(False)
            delete.setToolTip("Admin accounts can't be deleted from the app.")
        self.privacy.add_row("Delete account", "Removes your settings and trades from the Signa server", delete)

    # --- acciones ---------------------------------------------------------------------------------
    def _job(self, fn, *args, then):
        j = Job(fn, *args)
        j.done.connect(then)
        self.jobs = self.jobs[-10:] + [j]
        j.start()

    def _read(self, kind: str, version: int, btn):
        btn.setEnabled(False)

        def done(text, err):
            btn.setEnabled(True)
            if err:
                QMessageBox.warning(self, "Terms", err)
            else:
                TermsDialog(self, f"{TERMS.get(kind, kind)} · version {version}", text).exec()
        self._job(net.terms_text, kind, version, then=done)

    def _delete(self):
        dlg = DeleteDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.delete_requested.emit(dlg.local.isChecked())

    def _send_logs(self, btn):
        box = QMessageBox(self)
        box.setWindowTitle("Send logs")
        box.setText("Send Signa's logs to the Signa admin?")
        box.setInformativeText("They include what Signa did on this computer: calls received, orders, your IBKR "
                               "account number and errors. Never passwords or bot tokens.")
        yes = box.addButton("Send", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        btn.setEnabled(False)
        btn.setText("Sending…")

        def done(_res, err):
            btn.setEnabled(True)
            btn.setText("✕ Failed" if err else "✓ Sent")
            btn.setToolTip(err)
        self._job(lambda: net.send_logs(logs_blob()), then=done)

    def _set_boot(self, on: bool):
        try:
            if not on:
                autostart.disable()
                return
            autostart.enable()
            old = autostart.old_gateway_shortcuts()
            if old:
                box = QMessageBox(self)
                box.setWindowTitle("Start with Windows")
                box.setText("Turn off your old IB Gateway startup shortcuts?")
                box.setInformativeText(f"{', '.join(n.removesuffix('.lnk') for n in old)} also start the Gateways "
                                       "when you sign in. With Signa starting them too, IBKR would log one of "
                                       "them out. They aren't deleted: you can turn them on again in Task Manager "
                                       "› Startup apps.")
                yes = box.addButton("Turn them off", QMessageBox.AcceptRole)
                box.addButton("Keep them", QMessageBox.RejectRole)
                box.exec()
                if box.clickedButton() is yes:
                    autostart.disable_shortcuts(old)
        except OSError as e:
            QMessageBox.warning(self, "Start with Windows", f"Windows didn't allow it: {e}")
            self.boot.blockSignals(True)
            self.boot.setChecked(autostart.is_enabled())
            self.boot.blockSignals(False)

