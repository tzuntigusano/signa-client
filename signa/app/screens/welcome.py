"""Welcome: clave de invitación y las dos aceptaciones obligatorias. Sin aceptar no se entra."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QWidget

from .. import theme
from ..widgets import Card, button, hbox, icon, label, logo, vbox, widget


class WelcomeScreen(QWidget):
    done = Signal(str)  # la clave de invitación

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

        lock = QLabel()
        lock.setPixmap(icon("lock", theme.current["mu"], 14).pixmap(14, 14))
        foot = hbox(lock, label("Your IBKR login stays on this computer, in the system keychain.", "small"), None,
                    spacing=8)

        column = widget(vbox(brand, intro, card, foot, spacing=22))
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

    def fail(self, text: str):
        """Error del servidor al canjear la invitación."""
        self._fail(text)

    def _fail(self, text: str):
        self.error.setText(text)
        self.error.show()
