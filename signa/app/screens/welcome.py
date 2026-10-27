"""Welcome: clave de invitación y las dos aceptaciones obligatorias. Sin aceptar no se entra. Sin clave, se pide
con Discord (discord_login): queda pendiente y el admin la pasa por Discord."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QWidget

from .. import discord_login, theme
from ..widgets import Card, button, hbox, icon, label, logo, vbox, widget


class WelcomeScreen(QWidget):
    done = Signal(str)  # la clave de invitación
    _asked = Signal(dict)  # respuesta de la solicitud con Discord (llega desde su hilo)
    _ask_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")

        mark = QLabel()
        mark.setPixmap(logo(36))
        brand = hbox(mark, label("Signa", "h1"), None, spacing=12)

        intro = vbox(label("Set up this computer", "h1"),
                     label("Enter the invite key you received. Signa links this computer to your account.", "mu",
                           wrap=True), spacing=6)

        self.key = QLineEdit()
        self.key.setProperty("mono", "true")
        self.key.setPlaceholderText("SGN-4F7K-92QD")
        self.data_ok = QCheckBox("I agree to send the calls I receive, their prices and my executions to the "
                                 "Signa server. Required to use Signa.")
        self.terms_ok = QCheckBox("I accept the terms of use.")
        self.error = label("", tone="bad")
        self.error.hide()
        go = button("Continue", "primary")
        go.setMinimumHeight(40)
        go.clicked.connect(self._continue)

        card = Card(padding=22)
        card.lay.setSpacing(16)
        card.add(vbox(label("Invite key", "name"), self.key, spacing=6))
        card.add(self._wrap_check(self.data_ok))
        card.add(self.terms_ok)
        card.add(self.error)
        card.add(go)

        self.ask_btn = button("Request access with Discord")
        self.ask_btn.clicked.connect(self._ask)
        self.ask_msg = label("Log in with Discord and the admin gets your request. They'll send you the key there.",
                             "small", wrap=True)
        ask = Card(padding=18)
        ask.lay.setSpacing(10)
        ask.add(label("No invite key yet?", "name"))
        ask.add(self.ask_msg)
        ask.add(self.ask_btn)
        self._asked.connect(self._on_asked)
        self._ask_failed.connect(self._on_ask_failed)

        lock = QLabel()
        lock.setPixmap(icon("lock", theme.current["mu"], 14).pixmap(14, 14))
        foot = hbox(lock, label("Your IBKR login stays on this computer, in the system keychain.", "small"), None,
                    spacing=8)

        column = widget(vbox(brand, intro, card, ask, foot, spacing=22))
        column.setFixedWidth(460)
        outer = vbox(None, hbox(None, column, None), None)
        self.setLayout(outer)

    @staticmethod
    def _wrap_check(cb: QCheckBox) -> QWidget:
        # QCheckBox no parte líneas: la casilla va sola y el texto en una etiqueta que sí lo hace
        text = label(cb.text(), wrap=True)
        text.mousePressEvent = lambda _e: cb.toggle()
        cb.setText("")
        cb.setFixedSize(18, 18)
        row = hbox(cb, spacing=10)
        row.addWidget(text, 1)
        row.setAlignment(cb, Qt.AlignTop)
        return widget(row)

    def _continue(self):
        if not self.key.text().strip():
            return self._fail("Enter your invite key.")
        if not (self.data_ok.isChecked() and self.terms_ok.isChecked()):
            return self._fail("Accept both to continue. Signa can't work without them.")
        self.error.hide()
        self.done.emit(self.key.text().strip())

    # --- pedir acceso con Discord -------------------------------------------------------------------
    def _ask(self):
        self.ask_btn.setEnabled(False)
        self.ask_btn.setText("Waiting for Discord in your browser…")
        discord_login.run(self._asked.emit, self._ask_failed.emit)

    def _on_asked(self, res: dict):
        handle = f"@{res.get('handle', '')}"
        self.ask_btn.setText("Request sent")
        self.ask_msg.setText({
            "pending": f"✓  Request sent as {handle}. The admin will send you your invite key on Discord: enter it "
                       "above.",
            "approved": f"{handle} was already approved: the admin has your key. Ask them on Discord.",
            "rejected": f"The request from {handle} wasn't accepted.",
            "member": f"{handle} already has a Signa account. Enter your invite key above, or ask the admin for a "
                      "new one.",
        }.get(res.get("status"), "Request sent."))

    def _on_ask_failed(self, text: str):
        self.ask_btn.setText("Request access with Discord")
        self.ask_btn.setEnabled(True)
        self.ask_msg.setText("✕  " + text)

    def fail(self, text: str):
        """Error del servidor al canjear la invitación."""
        self._fail(text)

    def _fail(self, text: str):
        self.error.setText(text)
        self.error.show()
