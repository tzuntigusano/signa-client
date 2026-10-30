"""Telegram: un bot por proveedor, enlazado desde aquí. El token se queda en este PC (signa.core.bots); al servidor
solo va el @ del bot. El ejecutor recoge los cambios al vuelo y manda por ese bot las tarjetas del proveedor."""
import threading

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QGraphicsOpacityEffect, QLineEdit, QMessageBox, QWidget

from ...core import bots
from .. import local
from ..widgets import Card, button, hbox, label, section, tag, vbox, widget
from .base import Page


class Job(QObject):
    """Una llamada bloqueante a Telegram en un hilo; el resultado vuelve al hilo de la interfaz por la señal."""
    done = Signal(object, str)  # (resultado, error)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        try:
            self.done.emit(self.fn(*self.args), "")
        except Exception as e:  # cualquier fallo se enseña, no tumba la app; los «friendly» tal cual
            self.done.emit(None, str(e) if getattr(e, "friendly", False) else f"Something went wrong: {e}")


def open_url(url: str):
    QDesktopServices.openUrl(QUrl(url))


def clear(card: Card):
    while card.lay.count():
        w = card.lay.takeAt(0).widget()
        if w:
            w.hide()
            w.deleteLater()
    card._rows = 0


class LinkBotDialog(QDialog):
    """Asistente: 1) token (se comprueba con Telegram)  2) Start en el bot (se detecta el chat solo)  3) prueba."""

    def __init__(self, parent: QWidget, provider: dict):
        super().__init__(parent)
        self.prov = provider
        self.token = self.username = self.chat_id = None
        self.jobs: list[Job] = []
        self.waiting = False
        self.offset = None
        self.setWindowTitle(f"Link a Telegram bot · {provider['name']}")
        self.setObjectName("Page")
        self.setMinimumWidth(540)

        self.token_in = QLineEdit()
        self.token_in.setPlaceholderText("123456789:AAH…")
        self.token_in.setProperty("mono", "true")
        self.token_in.setEchoMode(QLineEdit.Password)
        self.token_in.returnPressed.connect(self._check)
        self.check_btn = button("Check", "primary")
        self.check_btn.clicked.connect(self._check)
        father = button("Open @BotFather")
        father.clicked.connect(lambda: open_url("https://t.me/BotFather"))
        self.s1 = label("", "small", wrap=True)
        step1 = widget(vbox(label("1 · Create the bot", "name"),
                            label(f"In Telegram, open @BotFather, send /newbot and pick any name (e.g. “Signa "
                                  f"{provider['name']}”). It replies with a token: paste it here.", "small",
                                  wrap=True),
                            hbox(self.token_in, self.check_btn, father), self.s1, spacing=8))

        self.open_bot = button("Open the bot")
        self.open_bot.clicked.connect(lambda: open_url(f"https://t.me/{self.username}"))
        self.retry = button("Try again")
        self.retry.clicked.connect(self._wait_start)
        self.s2 = label("", "small", wrap=True)
        self.step2 = widget(vbox(label("2 · Say hi to your bot", "name"),
                                 label("Open it in Telegram and press Start (if you already did, just send it any "
                                       "message). Signa picks up your chat by itself.", "small", wrap=True),
                                 hbox(self.open_bot, self.retry, None), self.s2, spacing=8))

        self.s3 = label("", "small", wrap=True)
        self.step3 = widget(vbox(label("3 · Done", "name"), self.s3, spacing=8))

        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        self.save_btn = button("Link bot", "primary")
        self.save_btn.clicked.connect(self._save)
        foot = hbox(label("The token stays on this computer.", "small"), None, cancel, self.save_btn)

        card = Card(padding=20)
        for part in (step1, self.step2, self.step3):
            card.add(part)
        self.setLayout(vbox(card, foot, spacing=14, margins=(18, 18, 18, 18)))
        self._stage(1)

    def _stage(self, n: int):
        self.step2.setEnabled(n >= 2)
        self.step3.setVisible(n >= 3)
        self.retry.setVisible(False)
        self.save_btn.setEnabled(n >= 3)

    def _job(self, fn, *args, then):
        j = Job(fn, *args)
        j.done.connect(then)
        self.jobs.append(j)
        j.start()

    # --- paso 1
    def _check(self):
        token = self.token_in.text().strip()
        if not token:
            return
        if (other := bots.owner_of(token)) and other != self.prov["id"]:
            self.s1.setText(f"✕  This bot is already linked to {other}. Each provider needs its own bot.")
            return
        self.waiting = False
        self.check_btn.setEnabled(False)
        self.s1.setText("Checking with Telegram…")
        self._job(bots.check_token, token, then=lambda res, err: self._checked(token, res, err))

    def _checked(self, token: str, username, err: str):
        self.check_btn.setEnabled(True)
        if err:
            self.s1.setText(f"✕  {err}")
            return
        self.token, self.username, self.offset = token, username, None
        self.s1.setText(f"✓  Bot found: @{username}")
        self.open_bot.setText(f"Open @{username}")
        self._stage(2)
        if chat := bots.known_chat(token):  # ya iniciado antes: no hace falta otro Start
            self._polled((None, {"id": chat}), "")
            return
        self._wait_start()

    # --- paso 2: se pregunta a Telegram en bucle (cada consulta espera hasta 10 s) hasta ver tu Start
    def _wait_start(self):
        self.retry.setVisible(False)
        self.waiting = True
        self.s2.setText("Waiting for your Start (or any message) in the bot…")
        self._poll()

    def _poll(self):
        if self.waiting:
            self._job(bots.find_start, self.token, self.offset, then=self._polled)

    def _polled(self, res, err: str):
        if not self.waiting:
            return
        if err:
            self.waiting = False
            self.s2.setText(f"✕  {err}")
            self.retry.setVisible(True)
            return
        self.offset, chat = res
        if not chat:
            QTimer.singleShot(200, self._poll)
            return
        self.waiting = False
        self.chat_id = str(chat["id"])
        who = chat.get("first_name") or chat.get("username")
        self.s2.setText(f"✓  Got it, {who}." if who else "✓  Already started: using the same chat as before.")
        self.s3.setText("Sending a test message…")
        self._stage(3)
        self.save_btn.setEnabled(False)
        text = f"✅ Signa is linked. {self.prov['name']}'s cards will arrive in this chat."
        self._job(bots.send, self.token, self.chat_id, text, then=self._tested)

    def _tested(self, _res, err: str):
        self.s3.setText(f"✕  {err}" if err else "✓  Test message sent: check your Telegram. Press Link bot to "
                                                "finish.")
        self.save_btn.setEnabled(not err)

    def _save(self):
        bots.link(self.prov["id"], self.token, self.chat_id, self.username)
        self.accept()

    def done(self, r: int):
        self.waiting = False  # la consulta en curso acaba sola (≤10 s) y ya no se repite
        super().done(r)


class TelegramScreen(Page):
    bot_changed = Signal(str, object)  # (proveedor, "@bot" o None) -> se guarda el @ en el servidor

    def __init__(self):
        super().__init__("Telegram")
        self.providers: list[dict] = []
        self.jobs: list[Job] = []
        self.box = Card()
        self.add(section("Your bots", self.box))
        self.status = Card()
        self.add(section("Delivery", self.status))
        self.add(label("Each provider gets its own bot: Telegram lets only one program read a bot's buttons, so a "
                       "bot can't be shared between providers or with another copier. Tokens stay on this "
                       "computer; Signa's server only knows each bot's @name.", "small", wrap=True))
        self.finish()
        self.set_providers([])
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._paint_status)
        self.timer.start(10_000)

    def set_providers(self, providers: list[dict]):
        self.providers = providers
        self.refresh()

    def refresh(self):
        clear(self.box)
        mine = bots.linked()
        if not self.providers:
            self.box.add_row("No providers yet", "Your providers show up here once the admin enables them.")
        # los que sigues primero; los que no, al final y apagados (su bot no recibe nada mientras no lo sigas)
        for p in sorted(self.providers, key=lambda x: not x.get("followed")):
            t = mine.get(p["id"])
            if not p.get("followed"):
                desc = (f"@{t.get('username') or '?'} · " if t else "") + "You don't follow this provider: no cards."
                row = self.box.add_row(p["name"], desc, tag("Not following", "neutral"))
                row.setEnabled(False)
                fade = QGraphicsOpacityEffect(row)
                fade.setOpacity(0.45)
                row.setGraphicsEffect(fade)
                row.setToolTip("Follow it in Providers to use its bot.")
                continue
            if t:
                desc = f"@{t.get('username') or '?'} · its cards reach your Telegram"
                test, change, remove = button("Send test"), button("Change"), button("Remove")
                test.clicked.connect(lambda _=False, p=p, b=test: self._test(p, b))
                change.clicked.connect(lambda _=False, p=p: self._link(p))
                remove.clicked.connect(lambda _=False, p=p: self._remove(p))
                ctl = widget(hbox(tag("Linked", "good", dot=True), test, change, remove))
            else:
                desc = "No bot yet: you won't get this provider's cards on your phone."
                ctl = button("Link a bot", "primary")
                ctl.clicked.connect(lambda _=False, p=p: self._link(p))
            self.box.add_row(p["name"], desc, ctl)
        self._paint_status()

    def _paint_status(self):
        clear(self.status)
        if local.executor_running():
            self.status.add_row("Signa executor", "Places the orders and sends the cards from this computer",
                                tag("Running", "good", dot=True))
            return
        btn = button("Start executor", "primary")

        def start():
            local.start_executor()
            btn.setEnabled(False)
            btn.setText("Starting…")
        btn.clicked.connect(start)
        self.status.add_row("Signa executor", "Not running: no cards reach your Telegram until it starts", btn)

    def _link(self, p: dict):
        dlg = LinkBotDialog(self, p)
        if dlg.exec() == QDialog.Accepted:
            self.bot_changed.emit(p["id"], f"@{dlg.username}")
            self.refresh()

    def _remove(self, p: dict):
        box = QMessageBox(self)
        box.setWindowTitle("Remove bot")
        box.setText(f"Stop sending {p['name']}'s cards to Telegram?")
        box.setInformativeText("The bot itself isn't deleted: you can link it again later.")
        yes = box.addButton("Remove", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            bots.unlink(p["id"])
            self.bot_changed.emit(p["id"], None)
            self.refresh()

    def _test(self, p: dict, btn):
        t = bots.linked().get(p["id"])
        if not t:
            return
        btn.setEnabled(False)
        btn.setText("Sending…")
        j = Job(bots.send_test_card, t["token"], t["chat_id"], p["name"])  # call falsa con botones que no operan

        def done(_res, err):
            btn.setEnabled(True)
            btn.setText("✕ Failed" if err else "✓ Sent")
            btn.setToolTip(err)
            QTimer.singleShot(4000, btn, lambda: btn.setText("Send test"))  # nada si ya no existe
        j.done.connect(done)
        self.jobs.append(j)
        j.start()
