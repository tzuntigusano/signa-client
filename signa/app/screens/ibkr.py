"""IBKR: tus dos IB Gateway (paper y live) gestionados desde Signa con el IBC que lleva dentro (signa.gateway):
conectar, desconectar y reiniciar, y el login guardado en el Administrador de credenciales de Windows. Más el estado
de la conexión del ejecutor y qué proveedores opera cada cuenta.

Parar o reiniciar un Gateway corta a TODO lo conectado a él (también tu copiador): siempre se pide confirmar."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from ... import gateway
from .. import local
from ..widgets import Card, button, columns, hbox, label, section, tag, vbox, widget
from .base import Page
from .telegram import Job, clear

ACCOUNTS = (("paper", "Paper account"), ("live", "Live account"))


def usd(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


def gateway_states() -> dict:
    """Estado de los dos Gateway (escanea procesos y el llavero: va en un hilo)."""
    out = {}
    for name, _ in ACCOUNTS:
        st = gateway.status(name)
        creds = gateway.credentials(name) if st["credentials"] else None
        st["user"] = gateway.masked(creds[0]) if creds else None
        st["can_import"] = (gateway.OLD_IBC / name / "config.ini").exists()
        out[name] = st
    return out


class LoginDialog(QDialog):
    """Usuario y contraseña de IBKR de una cuenta: van al Administrador de credenciales de Windows."""

    def __init__(self, parent, account: str, title: str):
        super().__init__(parent)
        self.account = account
        self.setWindowTitle(f"IBKR login · {title}")
        self.setObjectName("Page")
        self.setMinimumWidth(420)
        self.user = QLineEdit()
        self.user.setPlaceholderText("IBKR username")
        self.pw = QLineEdit()
        self.pw.setPlaceholderText("Password")
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.returnPressed.connect(self._save)
        self.msg = label("", "small", wrap=True)
        hint = ("Use the username of your paper account, or your live one (IBKR logs it into paper)."
                if account == "paper" else "Your live IBKR username. Logging in asks you to approve IB Key on your "
                                           "phone.")
        cancel, save = button("Cancel"), button("Save", "primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        card = Card(padding=20)
        card.add(vbox(label(hint, "small", wrap=True), self.user, self.pw, self.msg, spacing=10))
        self.setLayout(vbox(card, hbox(label("Saved in Windows Credential Manager, never in a file.", "small"), None,
                                       cancel, save), spacing=14, margins=(18, 18, 18, 18)))

    def _save(self):
        try:
            gateway.save_credentials(self.account, self.user.text(), self.pw.text())
        except gateway.GatewayError as e:
            self.msg.setText(f"✕  {e}")
            return
        self.pw.clear()
        self.accept()


class IbkrScreen(Page):
    def __init__(self):
        super().__init__("IBKR")
        self.providers: list[dict] = []
        self.gw: dict = {}
        self.busy: dict[str, str] = {}  # cuenta -> lo que se está haciendo ("Starting…")
        self.jobs: list[Job] = []
        self.polling = False
        self.exec_card = Card()
        self.add(section("Signa executor", self.exec_card))
        self.cards = {name: Card() for name, _ in ACCOUNTS}
        self.add(columns(*(section(title, self.cards[name]) for name, title in ACCOUNTS)))
        self.add(label("Signa runs IB Gateway with IBC built in: it logs in for you, keeps the Gateway alive through "
                       "its daily restart and lets you restart it without logging in again. Stopping or restarting "
                       "a Gateway disconnects everything using it, your copier too.", "small", wrap=True))
        self.finish()
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5_000)

    def set_providers(self, providers: list[dict]):
        self.providers = providers
        self.paint()

    # --- datos ------------------------------------------------------------------------------------
    def _job(self, fn, *args, then):
        j = Job(fn, *args)
        j.done.connect(then)
        self.jobs = [x for x in self.jobs if x is not j][-20:] + [j]
        j.start()

    def refresh(self):
        if self.polling:
            return
        self.polling = True
        self._job(gateway_states, then=self._got)

    def _got(self, states, err: str):
        self.polling = False
        if states:
            self.gw = states
            for name, st in states.items():  # lo que se pidió ya se ve hecho: fuera el «Starting…»
                b = self.busy.get(name, "")
                if b.startswith(("Starting", "Restarting")) and st["api"] or b.startswith("Stopping") and \
                        not st["running"]:
                    self.busy.pop(name, None)
        self.paint()

    # --- pintar -----------------------------------------------------------------------------------
    def paint(self):
        running = local.executor_running()
        st = local.read_status() if running else None
        self._executor(running, st)
        for name, title in ACCOUNTS:
            self._account(name, title, (st or {}).get("accounts", {}).get(name))

    def _executor(self, running: bool, st: dict | None):
        clear(self.exec_card)
        if running:
            server = st and st.get("server")
            self.exec_card.add_row("Running", "Places the orders and sends the cards from this computer",
                                   tag("Connected to Signa", "good", dot=True) if server
                                   else tag("Waiting for the Signa server", "warn"))
            return
        btn = button("Start executor", "primary")

        def start():
            local.start_executor()
            btn.setEnabled(False)
            btn.setText("Starting…")
        btn.clicked.connect(start)
        self.exec_card.add_row("Not running", "Without it Signa can't trade or send cards from this computer.", btn)

    def _account(self, name: str, title: str, a: dict | None):
        card = self.cards[name]
        clear(card)
        users = [p["name"] for p in self.providers if p.get("followed") and p.get("account") == name]
        card.add_row("Providers", ", ".join(users) or "None of your providers trade here", None)
        g = self.gw.get(name)
        if g is None:
            card.add_row("IB Gateway", "Checking…", None)
            return
        card.add_row("IB Gateway", self._gw_desc(name, g), self._gw_controls(name, title, g))
        card.add_row("Login", f"Saved · {g['user']}" if g["user"] else "Not saved yet", self._login_controls(
            name, title, g))
        if a is None or not g["api"]:
            return
        if a["connected"]:
            conn, desc = tag(a.get("account") or "Connected", "good", dot=True), f"Client ID {a['client_id']}"
        elif a["used"]:
            conn, desc = tag("Not connected", "bad"), "Signa needs it: reconnects on its next order"
        else:
            conn, desc = tag("Not in use", "neutral"), "Connects when a provider of yours trades here"
        card.add_row("Signa connection", desc, conn)
        if a["connected"]:
            card.add_row("Net liquidation", None, label(usd(a.get("net_liq")), "name"))
            card.add_row("Available funds", None, label(usd(a.get("available")), "name"))
            card.add_row("Open positions · orders", None,
                         label(f"{a.get('positions', 0)} · {a.get('orders', 0)}", "name"))

    @staticmethod
    def _gw_desc(name: str, g: dict) -> str:
        port = f"Port {gateway.ACCOUNTS[name]['api']}"
        if not g["running"]:
            return f"{port} · stopped"
        who = "run by Signa" if g["managed"] == "signa" else "started outside Signa (old IBC)"
        return f"{port} · {who}" + ("" if g["api"] else " · logging in…")

    def _gw_controls(self, name: str, title: str, g: dict):
        if name in self.busy:
            return tag(self.busy[name], "warn")
        if not g["running"]:
            btn = button("Connect", "primary")
            btn.setEnabled(g["credentials"])
            btn.setToolTip("" if g["credentials"] else "Save your IBKR login first")
            btn.clicked.connect(lambda: self._connect(name, title))
            return btn
        restart, stop = button("Restart"), button("Disconnect")
        restart.clicked.connect(lambda: self._restart(name, title, g))
        stop.clicked.connect(lambda: self._stop(name, title))
        state = tag("Running", "good", dot=True) if g["api"] else tag("Logging in", "warn")
        return widget(hbox(state, restart, stop))

    def _login_controls(self, name: str, title: str, g: dict):
        edit = button("Change" if g["user"] else "Add login", None if g["user"] else "primary")
        edit.clicked.connect(lambda: self._login(name, title))
        items = [edit]
        if g["can_import"] and not g["user"]:
            imp = button("Import from IBC")
            imp.setToolTip(f"Copies the username and password saved in {gateway.OLD_IBC}\\{name}\\config.ini")
            imp.clicked.connect(lambda: self._import(name))
            items.insert(0, imp)
        return widget(hbox(*items))

    # --- acciones ---------------------------------------------------------------------------------
    def _confirm(self, title: str, text: str, info: str, ok: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setInformativeText(info)
        yes = box.addButton(ok, QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _users_of(self, name: str) -> str:
        users = [p["name"] for p in self.providers if p.get("followed") and p.get("account") == name]
        return (f"Signa ({', '.join(users)}), " if users else "") + "your copier and any other app using this Gateway"

    def _run(self, name: str, busy: str, fn, *args):
        self.busy[name] = busy
        self.paint()

        def done(_res, err):
            if err:
                self.busy.pop(name, None)
                QMessageBox.warning(self, "IBKR", err)
            self.refresh()
        self._job(fn, *args, then=done)
        QTimer.singleShot(180_000, lambda: self.busy.pop(name, None))  # nunca se queda «Starting…» para siempre

    def _connect(self, name: str, title: str):
        if name == "live" and not self._confirm("Connect live", "Start the live IB Gateway?",
                                                "IBKR will ask you to approve the login with IB Key on your phone.",
                                                "Connect"):
            return
        self._run(name, "Starting…" + (" approve IB Key" if name == "live" else ""), local.start_gateway, name)

    def _stop(self, name: str, title: str):
        if self._confirm("Disconnect", f"Close the {title.lower().replace(' account', '')} IB Gateway?",
                         f"This disconnects {self._users_of(name)}. Your positions and orders at IBKR stay as they "
                         "are.", "Disconnect"):
            self._run(name, "Stopping…", local.stop_gateway, name)

    def _restart(self, name: str, title: str, g: dict):
        outside = g["managed"] != "signa"
        info = f"This briefly disconnects {self._users_of(name)}."
        if outside:
            info += (" It was started outside Signa, so it's closed and started again from Signa"
                     + (": you'll approve IB Key on your phone." if name == "live" else "."))
        else:
            info += " No new login is needed."
        if self._confirm("Restart", f"Restart the {title.lower().replace(' account', '')} IB Gateway?", info,
                         "Restart"):
            self._run(name, "Restarting…", local.restart_gateway, name)

    def _login(self, name: str, title: str):
        if LoginDialog(self, name, title).exec() == QDialog.Accepted:
            self.refresh()

    def _import(self, name: str):
        self._job(gateway.import_credentials, name,
                  then=lambda _u, err: QMessageBox.warning(self, "IBKR", err) if err else self.refresh())
