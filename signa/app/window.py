"""Ventana principal: barra lateral + pantallas. Sin clave guardada enseña Welcome (alta con invitación).
El panel de Admin solo existe en Windows: la versión de Mac ni lo trae (tools/build.py, tools/export_client.py)."""
import json
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import __version__
from ..core import runtime
from . import brand
from . import client as net
from . import theme
from .screens.calls import CallsScreen
from .screens.ibkr import IbkrScreen
from .screens.mycalls import MyCallsScreen
from .screens.overview import OverviewScreen
from .screens.providers import ProvidersScreen
from .screens.settings import SettingsScreen, whats_new
from .screens.stats import StatsScreen
from .screens.telegram import TelegramScreen
from .screens.drawer import TradeDrawer
from .screens.trades import TradesScreen
from .screens.welcome import WelcomeScreen
from .widgets import hbox, icon, label, logo

# (clave, texto); None = hueco entre grupos
NAV = [("overview", "Overview"), ("providers", "Providers"), ("trades", "Trades"), ("stats", "Stats"),
       ("calls", "Calls"), ("mycalls", "My calls"), None,
       ("ibkr", "IBKR"), ("telegram", "Telegram"), ("settings", "Settings")]
HAS_ADMIN = not runtime.IS_MAC
UPDATE_SNOOZE = 6  # h: tras «Later», el aviso de versión nueva vuelve a salir pasado este tiempo
if HAS_ADMIN:
    from .screens.admin import AdminScreen
    NAV.append(("admin", "Admin"))
STATUS = {"connecting": ("Connecting…", "warn"), "online": ("Server · online", "good"),
          "offline": ("Server · offline, retrying", "bad"), "rejected": ("Server · key not valid", "bad")}


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication, client_factory=net.Client, services: bool = False, demo: list | None = None):
        super().__init__()
        self.demo = demo  # --demo: trades de ejemplo SOLO en pantalla (ni servidor ni base de datos)
        self.app = app
        self.services = services  # arrancar agente/ejecutor/Gateways al conectar (la app real; no tests ni capturas)
        self.jobs: list = []
        self.client_factory = client_factory
        self.client = None
        self.bot_reqs: set[int] = set()  # guardados del @ de un bot (pantalla Telegram)
        self.prefs = QSettings("Signa", "Signa")
        self.theme_mode = self.prefs.value("theme", "system")
        self.current = "overview"
        self.state = {"user": "", "providers": [], "signals": [], "status": "connecting", "consents": None,
                      "app_latest": None, "app_changes": [], "role": None}
        self.update_offered: dict[str, float] = {}  # versión -> hasta cuándo no se vuelve a avisar («Later»)
        self.update_box = None  # el aviso de versión nueva, si está abierto
        self.key_offered = False  # el aviso de «clave no válida» ya salió en esta sesión
        if demo is not None:
            self.state["trades"] = demo
        self.quitting = False
        self.wipe_local = False
        self.tray = self._tray() if services else None
        self.demo_quotes: dict[str, dict] = {}
        self.selling: set[str] = set()  # «Sell all» de Trades en curso: su resultado sale encima de la tabla
        self.setWindowTitle("Signa")
        self.resize(1440, 860)
        self.setMinimumSize(1280, 720)  # Trades lleva muchas columnas (y llevará más)
        theme.apply(app, self.theme_mode)
        app.styleHints().colorSchemeChanged.connect(lambda _s: self._retheme() if self.theme_mode == "system" else None)
        self._build()
        if net.saved_key():
            self._connect()

    # --- servidor ------------------------------------------------------------------------------
    def _connect(self, new_key: bool = False):
        self.client = self.client_factory()
        self.client.status.connect(self._on_status)
        self.client.welcome.connect(self._on_welcome)
        self.client.signal.connect(self._on_signal)
        self.client.trades.connect(self._on_trades)
        self.client.saved.connect(self._on_saved)
        self.client.reply.connect(self._on_reply)
        self.client.alert.connect(self._on_alert)
        self.client.start()
        if self.services:
            from ..core.logs import Log
            from .local import start_services
            from .screens.telegram import Job
            log = Log("app")
            j = Job(lambda: start_services(new_key))
            j.done.connect(lambda res, err: log("app", f"started: {', '.join(res)}" if res else err or "all running"))
            self.jobs.append(j.start())

    def _on_status(self, st: str):
        self.state["status"] = st
        if st == "rejected" and not net.saved_key():
            return
        self._paint_status()
        if self.screens:
            self.screens["overview"].set_data(status=st)
        if st == "rejected" and not self.key_offered and self.demo is None:
            self.key_offered = True  # una vez por sesión (también está en Settings › Invite key)
            self._new_key(rejected=True)

    def _new_key(self, rejected: bool = False):
        """«Use a new invite key»: olvida SOLO la clave de este PC y enseña Welcome para meter la nueva (la misma
        cuenta: historial y proveedores siguen). Bots de Telegram, logins de IBKR y ajustes se quedan. El ejecutor
        relee la clave del llavero al reintentar, así que se pone al día solo."""
        box = QMessageBox(self)
        box.setWindowTitle("Signa key")
        box.setIcon(QMessageBox.Warning if rejected else QMessageBox.Question)
        box.setText("This computer's Signa key isn't valid anymore" if rejected else "Use a new invite key?")
        box.setInformativeText(("The admin gave you a new key, or your access was paused. " if rejected else "")
                               + "Enter the new invite key the admin sent you. Your Telegram bots, IBKR logins and "
                               "settings stay on this computer.")
        go = box.addButton("Enter a new invite key", QMessageBox.AcceptRole)
        box.addButton("Later" if rejected else "Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return
        if self.client is not None:
            self.client.stop()
            self.client = None
        net.forget_key()
        self._build()  # sin clave: Welcome; al registrarse, _register reconecta

    def _on_welcome(self, d: dict):
        """Al conectar y en cada sincronización. Los proveedores con cambios SIN guardar no se pisan."""
        self.state.update(user=d.get("user", self.state["user"]), providers=d.get("providers", []),
                          signals=d.get("signals", []))
        if "trades" in d and self.demo is None:
            self.state["trades"] = d["trades"]
        for k in ("consents", "app_latest", "app_changes", "role", "risk"):
            if k in d:
                self.state[k] = d[k]
        if self.screens and "admin" in self.nav_buttons:
            self.nav_buttons["admin"].setVisible(self.state.get("role") == "admin")
        if self.screens:
            self.screens["settings"].changes = self.state["app_changes"]
            self.screens["settings"].set_account(d.get("consents"), d.get("app_latest"), d.get("role"))
            QTimer.singleShot(0, self._offer_update)  # fuera de la señal: el aviso es una ventana modal
        if self.screens and self.screens["providers"].has_unsaved():
            self.screens["calls"].set_signals(self.state["signals"])
            self.screens["overview"].set_data(signals=self.state["signals"])
            self._on_trades(self.state.get("trades", []))
            return
        self._fill()

    def _on_trades(self, trades: list):
        if self.demo is not None:
            return
        self.state["trades"] = trades
        if self.screens:
            self.screens["trades"].set_data(trades, self.state["providers"])
            self.screens["overview"].set_data(trades=trades)

    def _on_signal(self, s: dict):
        self.state["signals"].insert(0, s)
        if self.screens:
            self.screens["calls"].add_signal(s)
            self.screens["overview"].set_data(signals=self.state["signals"])
            self.screens["trades"].provider_message(s.get("provider") or "")  # su cronología, al momento

    def _fill(self):
        if not self.screens:
            return
        self.screens["providers"].set_providers(self.state["providers"])
        self.screens["calls"].set_providers(self.state["providers"])
        self.screens["calls"].set_signals(self.state["signals"])
        self.screens["overview"].set_data(self.state["providers"], self.state["signals"], self.state["status"])
        self.screens["telegram"].set_providers(self.state["providers"])
        self.screens["ibkr"].set_providers(self.state["providers"])
        self.screens["stats"].set_providers(self.state["providers"])
        self.screens["trades"].set_data(self.state.get("trades", []), self.state["providers"])
        self.screens["overview"].set_data(trades=self.state.get("trades", []))
        self.screens["settings"].set_account(self.state["consents"], self.state["app_latest"], self.state["role"])
        if self.state.get("risk"):
            self.screens["settings"].set_risk(self.state["risk"])
            self.screens["overview"].set_data(risk=self.state["risk"])
        self._paint_status()

    def _on_saved(self, res: dict):
        if not self.screens:
            return
        if res.get("req") in self.bot_reqs:  # el @ de un bot (pantalla Telegram): no es un «Save» de Providers
            self.bot_reqs.discard(res["req"])
            if res.get("ok"):
                p = res["provider"]
                self.state["providers"] = [p if x["id"] == p["id"] else x for x in self.state["providers"]]
                if not self.screens["providers"].has_unsaved():
                    self.screens["providers"].set_providers(self.state["providers"])
            return
        self.screens["providers"].saved(res)
        if res.get("ok"):  # p. ej. dejar de seguir: la pantalla Telegram lo refleja ya
            p = res["provider"]
            self.state["providers"] = [p if x["id"] == p["id"] else x for x in self.state["providers"]]
            self.screens["telegram"].set_providers(self.state["providers"])
            self.screens["ibkr"].set_providers(self.state["providers"])

    def _bot_changed(self, provider: str, bot: str | None):
        if self.client is not None:
            self.bot_reqs.add(self.client.save_subscription(provider, {"telegram_bot": bot}))

    def _save(self, provider: str, fields: dict):
        if self.client is None:
            self.screens["providers"].saved({"ok": False, "error": "Not connected to the Signa server."})
            return
        self.client.save_subscription(provider, fields)

    # --- montaje -------------------------------------------------------------------------------
    def _build(self):
        self.setWindowIcon(brand.icon())
        self.screens = {}
        if not net.saved_key():
            welcome = WelcomeScreen()
            welcome.done.connect(self._register)
            self.setCentralWidget(welcome)
            return
        root = QWidget()
        root.setObjectName("Window")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        self.screens = {
            "overview": OverviewScreen(), "providers": ProvidersScreen(), "trades": TradesScreen(),
            "stats": StatsScreen(),
            "calls": CallsScreen(), "mycalls": MyCallsScreen(), "ibkr": IbkrScreen(), "telegram": TelegramScreen(),
            "settings": SettingsScreen(self.theme_mode),
        }
        if HAS_ADMIN:
            self.screens["admin"] = AdminScreen()
            self.screens["admin"].admin_request.connect(
                lambda kind, data: self.client.request(kind, **data) if self.client else None)
        self.screens["settings"].theme_changed.connect(self._set_theme)
        self.screens["trades"].detail_requested.connect(self._trade_detail)
        self.screens["trades"].trade_requested.connect(self._open_trade)
        self.screens["trades"].quotes_requested.connect(self._trade_quotes)
        self.screens["trades"].sell_all_requested.connect(self._sell_all)
        self.screens["stats"].stats_requested.connect(
            lambda period: self.client.request("stats", period=period) if self.client else None)
        if self.demo is not None:
            self.screens["trades"].set_demo()
            self.setWindowTitle("Signa · DEMO DATA")
        self.screens["settings"].check_updates.connect(lambda: self.client.sync_now() if self.client else None)
        self.screens["settings"].export_requested.connect(lambda: self.client.request("export") if self.client
                                                          else None)
        self.screens["settings"].delete_requested.connect(self._delete_account)
        self.screens["settings"].new_key_requested.connect(self._new_key)
        self.screens["settings"].pair_requested.connect(
            lambda: self.client.request("pair_start", key=net.saved_key() or "") if self.client else
            QMessageBox.warning(self, "Link my phone", "Not connected to the Signa server."))
        self.screens["settings"].update_ready.connect(self._quit)  # el instalador la vuelve a abrir
        self.screens["settings"].risk_save.connect(
            lambda f: self.client.request("save_risk", fields=f) if self.client else None)
        self.screens["overview"].pause_requested.connect(
            lambda on: self.client.request("save_risk", fields={"paused": on}) if self.client else None)
        self.screens["overview"].close_all_requested.connect(
            lambda: self.client.request("close_all") if self.client else None)
        self.screens["providers"].save.connect(self._save)
        self.screens["mycalls"].quote_requested.connect(
            lambda o: self.client.request("own_quote", **o) if self.client else
            self.screens["mycalls"].set_quote(None, "Not connected to the Signa server."))
        self.screens["mycalls"].buy_requested.connect(self._own_buy)
        self.screens["telegram"].bot_changed.connect(self._bot_changed)
        for w in self.screens.values():
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self._go(self.current)
        if self.state["providers"] or self.state["signals"]:
            self._fill()

    def _sidebar(self) -> QWidget:
        c = theme.current
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(216)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(2)
        mark = QLabel()
        mark.setPixmap(logo(24))
        lay.addLayout(hbox(mark, label("Signa", "h2"), None, spacing=10, margins=(10, 6, 10, 22)))
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        for item in NAV:
            if item is None:
                lay.addSpacing(14)
                continue
            key, text = item
            b = QPushButton(text)
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icon(key, c["mu"]))
            b.clicked.connect(lambda _=False, k=key: self._go(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        foot = QFrame()
        foot.setStyleSheet(f"QFrame {{ border-top: 1px solid {c['line']}; }} QLabel {{ border: 0; }}")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(10, 14, 10, 2)
        fl.setSpacing(8)
        self.server_lb = label("", "small")
        self.user_lb = label(f"Signa · v{__version__}", "small")
        fl.addWidget(self.server_lb)
        fl.addWidget(self.user_lb)
        lay.addWidget(foot)
        self._paint_status()
        if "admin" in self.nav_buttons:
            self.nav_buttons["admin"].setVisible(self.state.get("role") == "admin")  # solo para cuentas admin
        return side

    def _paint_status(self):
        if not self.screens:
            return
        text, tone = STATUS.get(self.state["status"], STATUS["connecting"])
        self.server_lb.setText(f'<span style="color:{theme.current[tone]}">●</span>&nbsp;&nbsp;{text}')

    # --- acciones ------------------------------------------------------------------------------
    def _offer_update(self):
        """AVISO de versión nueva (al conectar, en cada sync y al publicarse una: el servidor manda «resync»). Una
        vez por versión; «Later» lo vuelve a enseñar a las UPDATE_SNOOZE horas (la app suele estar días abierta)."""
        from .screens.settings import version_tuple
        latest = self.state.get("app_latest")
        if (not latest or not runtime.FROZEN or self.demo is not None
                or version_tuple(latest) <= version_tuple(__version__)
                or time.time() < self.update_offered.get(latest, 0) or self.update_box is not None):
            return
        from ..core.market import market_open
        self.update_offered[latest] = time.time() + UPDATE_SNOOZE * 3600
        box = self.update_box = QMessageBox(self)
        box.setWindowTitle("Signa update")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Signa v{latest} is available")
        box.setInformativeText(whats_new(self.state.get("app_changes"), latest)
                               + f"<p>You have v{__version__}. Installing takes about a minute: Signa closes and "
                               "opens again on the new version. Your stops stay in IBKR.</p>"
                               + ("<p>The US market is OPEN right now: you can install it later from Settings.</p>"
                                  if market_open() else ""))
        go = box.addButton("Install now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        self.update_box = None
        if box.clickedButton() is go:
            self._go("settings")
            self.screens["settings"].install_now()

    def _go(self, key: str):
        self.current = key
        c = theme.current
        for k, b in self.nav_buttons.items():
            b.setChecked(k == key)
            b.setIcon(icon(k, c["tx"] if k == key else c["mu"]))
        self.stack.setCurrentWidget(self.screens[key])

    def _register(self, invite: str):
        welcome = self.centralWidget()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            net.register(invite)
        except net.RegisterError as e:
            welcome.fail(str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._build()
        self._connect(new_key=True)  # el ejecutor que corra aún lleva la clave vieja: se reinicia

    def _set_theme(self, mode: str):
        self.theme_mode = mode
        self.prefs.setValue("theme", mode)
        self._retheme()

    def _retheme(self):
        theme.apply(self.app, self.theme_mode)
        self._build()  # los iconos y colores pintados a mano se rehacen con el tema nuevo

    # --- cuenta (Settings) -----------------------------------------------------------------------
    def _on_reply(self, d: dict):
        if d.get("type") == "export":
            if not d.get("ok"):
                QMessageBox.warning(self, "Export my data", d.get("error") or "Couldn't export.")
                return
            path, _ = QFileDialog.getSaveFileName(self, "Export my data", "signa-my-data.json", "JSON (*.json)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(d["data"], f, indent=2, ensure_ascii=False, default=str)
        elif str(d.get("type", "")).startswith("admin_") and "admin" in self.screens:
            adm = self.screens["admin"]
            if d["type"] in ("admin_user_status", "admin_user_logs"):  # Status & logs: a la ventana de ese usuario
                adm.diag_reply(d)
            elif not d.get("ok"):
                adm.note(d.get("error") or "Failed", ok=False)
            elif d["type"] == "admin_state":
                adm.set_state(d)
            else:
                if d["type"] == "admin_invite":
                    adm.show_invite(d["name"], d["code"])
                elif d["type"] in ("admin_access", "admin_reissue") and d.get("code"):
                    adm.show_invite(d["name"], d["code"], d.get("handle"))
                adm.note("Saved")
                self.client.request("admin_state")
        elif d.get("type") == "pair_start":
            self._show_pair(d)
        elif d.get("type") == "own_quote":
            if self.screens:
                self.screens["mycalls"].set_quote(d.get("quote") if d.get("ok") else None, d.get("error") or "")
        elif d.get("type") == "own_buy":
            if self.screens:
                self.screens["mycalls"].set_result(bool(d.get("ok")), d.get("note") or d.get("error") or "")
            if d.get("ok") and self.client is not None:
                self.client.sync_now()
        elif d.get("type") == "stats":
            self.screens["stats"].set_report(d.get("report") if d.get("ok") else None, d.get("error") or "")
        elif d.get("type") == "risk":
            if d.get("ok"):
                self.state["risk"] = d["risk"]
                self.screens["settings"].set_risk(d["risk"], "✓  Saved")
                self.screens["overview"].set_data(risk=d["risk"])
            else:
                self.screens["settings"].risk_msg.setText(f"✕  {d.get('error')}")
        elif d.get("type") == "close_all":
            sold, errors = d.get("done") or [], d.get("errors") or []
            text = f"{len(sold)} trades sold." + (f"\n\n{len(errors)} couldn't be sold:\n" + "\n".join(errors)
                                                  if errors else "")
            (QMessageBox.warning if errors else QMessageBox.information)(self, "Close all", text)
        elif d.get("type") == "trade_quote":
            self.drawer.set_quote(d.get("order_ref", ""), d.get("quote") if d.get("ok") else None, d.get("error", ""))
        elif d.get("type") == "trade_quotes":
            if self.screens:
                self.screens["trades"].set_quotes(d.get("quotes") or {})
        elif d.get("type") == "trade_action":
            ref = d.get("order_ref", "")
            self.drawer.set_result(ref, bool(d.get("ok")), d.get("note") or d.get("error") or "")
            if ref in self.selling and self.screens:  # «Sell all» de la tabla: el resultado, encima de ella
                self.selling.discard(ref)
                self.screens["trades"].note(d.get("note") or d.get("error") or "", bool(d.get("ok")))
            self._after_action(ref)
        elif d.get("type") == "trade_detail":
            if self.screens:
                self.screens["trades"].set_detail(d.get("order_ref", ""), d.get("events") if d.get("ok") else None,
                                                  d.get("error") or "")
        elif d.get("type") == "delete_account":
            if not d.get("ok"):
                QMessageBox.warning(self, "Delete account", d.get("error") or "Couldn't delete the account.")
                return
            self._forget_everything()

    def _show_pair(self, d: dict):
        """Settings › Phone: el código que se escribe en el móvil (una vez, durante unos minutos)."""
        if not d.get("ok"):
            QMessageBox.warning(self, "Link my phone", d.get("error") or "Couldn't make a code.")
            return
        code = f"{d['code'][:4]} {d['code'][4:]}"
        box = QMessageBox(self)
        box.setWindowTitle("Link my phone")
        box.setText(f"Your code:  {code}")
        box.setInformativeText(f"Open Signa on your Android phone and type this code. It works once, for "
                               f"{d.get('minutes', 10)} minutes. Don't share it: it gives access to your account.")
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addButton(QMessageBox.Close)
        box.exec()

    def _on_alert(self, d: dict):
        """Aviso de Signa (cuadre con IBKR, error de un motor, solicitud de acceso): a Overview y como notificación."""
        if self.screens:
            self.screens["overview"].add_alert(d)
            if d.get("kind") == "access" and "admin" in self.screens:  # el panel de admin, con la solicitud nueva
                self.client.request("admin_state")
        if self.tray is not None:
            self.tray.showMessage("Signa", d.get("text", ""), QSystemTrayIcon.Warning, 10000)

    # --- drawer «Trade» ----------------------------------------------------------------------------
    @property
    def drawer(self) -> TradeDrawer:
        if getattr(self, "_drawer", None) is None:
            self._drawer = TradeDrawer(self)
            self._drawer.quote_requested.connect(self._trade_quote)
            self._drawer.action_requested.connect(self._trade_action)
            self._drawer.closed.connect(lambda ref: self.client.request("trade_quote_stop", order_ref=ref)
                                        if self.client is not None and self.demo is None else None)
        return self._drawer

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_drawer", None) is not None and self._drawer.isVisible():
            self._drawer._layout()

    def _open_trade(self, ref: str):
        t = next((x for x in self.state.get("trades", []) if x["order_ref"] == ref), None)
        if t is None:
            return
        name = next((p["name"] for p in self.state["providers"] if p["id"] == t["provider_id"]),
                    "My calls" if t["provider_id"] == "mine" else t["provider_id"])
        self.drawer.open(t, name)

    def _trade_quote(self, ref: str):
        if self.demo is not None:
            from .demo import demo_quote
            self.demo_quotes[ref] = demo_quote(next(x for x in self.demo if x["order_ref"] == ref),
                                               self.demo_quotes.get(ref))
            self.drawer.set_quote(ref, self.demo_quotes[ref])
        elif self.client is not None:
            self.client.request("trade_quote", order_ref=ref)
        else:
            self.drawer.set_quote(ref, None, "Not connected to the Signa server.")

    def _trade_action(self, ref: str, action: str, value):
        q = self.demo_quotes.get(ref) if self.demo is not None else self.drawer.quote or {}
        if (q or {}).get("account") == "live":  # dinero real: siempre se confirma
            box = QMessageBox(self)
            box.setWindowTitle("Live account")
            box.setText(f"Send this to your LIVE IBKR account? ({action.replace('_', ' ')})")
            box.setInformativeText("It's a real order with real money.")
            yes = box.addButton("Send", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                self.drawer.set_result(ref, False, "Cancelled: nothing was sent.")
                return
        if self.demo is not None:
            from .demo import demo_action
            self.drawer.set_result(ref, True, demo_action(self.demo_quotes.get(ref) or {}, action, value))
            self.drawer.set_quote(ref, self.demo_quotes.get(ref))
        elif self.client is not None:
            self.client.request("trade_action", order_ref=ref, action=action, value=value)
        else:
            self.drawer.set_result(ref, False, "Not connected to the Signa server.")

    def _trade_quotes(self, refs: list):
        """Trades: el % de ahora de las abiertas (cada segundo, mientras se ve la pantalla)."""
        if self.demo is not None:
            from .demo import demo_quote
            for ref in refs:
                t = next((x for x in self.demo if x["order_ref"] == ref), None)
                if t is not None:
                    self.demo_quotes[ref] = demo_quote(t, self.demo_quotes.get(ref))
            self.screens["trades"].set_quotes({r: self.demo_quotes[r] for r in refs if r in self.demo_quotes})
        elif self.client is not None:
            self.client.request("trade_quotes", order_refs=refs)

    def _sell_all(self, ref: str):
        """Trades › «Sell all»: vender TODO lo de esa operación a mercado. Siempre se confirma (un clic en una tabla
        es fácil de dar sin querer); en la cuenta real, avisando de que es dinero de verdad."""
        t = next((x for x in self.state.get("trades", []) if x["order_ref"] == ref), None)
        if t is None:
            return
        live = t.get("account") == "live"
        box = QMessageBox(self)
        box.setWindowTitle("Sell all")
        box.setIcon(QMessageBox.Warning if live else QMessageBox.Question)
        box.setText(f"Sell all {t.get('held')} of {t.get('contract', '')} at market?")
        box.setInformativeText("LIVE account: a real order with real money." if live else "Paper account.")
        yes = box.addButton("Sell all", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        tr = self.screens["trades"]
        if self.demo is not None:
            from .demo import demo_action, demo_quote
            q = self.demo_quotes.get(ref) or demo_quote(t, None)
            self.demo_quotes[ref] = q
            tr.note(demo_action(q, "sell", q.get("held") or t.get("held") or 0))
        elif self.client is not None:
            self.selling.add(ref)
            tr.note(f"Selling all {t.get('held')} of {t.get('contract', '')} at market…")
            self.client.request("trade_action", order_ref=ref, action="sell", value="all")
        else:
            tr.note("Not connected to the Signa server.", False)

    def _own_buy(self, o: dict):
        """My calls › Buy (a mercado, o una orden de compra a tu precio si trae `limit`): siempre se confirma; en Live,
        avisando de que es dinero real."""
        live, limit = o["account"] == "live", o.get("limit")
        box = QMessageBox(self)
        box.setWindowTitle("My calls")
        box.setIcon(QMessageBox.Warning if live else QMessageBox.Question)
        if limit:
            box.setText(f"Buy order: {o['contracts']} {o['name']} at {limit:.2f}?")
            cost = (f"Up to ${limit * 100 * o['contracts']:,.0f}. Limit order, valid today: if it doesn't fill, "
                    "nothing is bought. ")
        else:
            box.setText(f"Buy {o['contracts']} {o['name']} at market?")
            cost = f"About ${o['ask'] * 100 * o['contracts']:,.0f} at the ask {o['ask']:.2f}. " if o.get("ask") else ""
        stop = f"Stop loss −{o['sl_pct']:g}%." if o.get("sl_pct") else "No stop loss."
        box.setInformativeText(cost + stop + ("\n\nLIVE account: a real order with real money." if live
                                              else "\n\nPaper account."))
        yes = box.addButton("Buy", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        scr = self.screens["mycalls"]
        if self.client is None:
            scr.set_result(False, "Not connected to the Signa server.")
            return
        scr.sending()
        self.client.request("own_buy", **{k: v for k, v in o.items() if k not in ("name", "ask")})

    def _after_action(self, ref: str):
        """Tras operar: su cronología y la lista se ponen al día (el servidor ya lo apuntó en el diario)."""
        tr = self.screens.get("trades") if self.screens else None
        if tr is not None:
            tr.details.pop(ref, None)
            if ref in tr.expanded:
                self._trade_detail(ref)
        if self.client is not None:
            self.client.sync_now()

    def _trade_detail(self, ref: str):
        if self.demo is not None:  # demo: una cronología de ejemplo, sin servidor
            from .demo import demo_detail
            t = next((x for x in self.demo if x["order_ref"] == ref), None)
            self.screens["trades"].set_detail(ref, demo_detail(t) if t else [])
        elif self.client is not None:
            self.client.request("trade_detail", order_ref=ref)
        else:
            self.screens["trades"].set_detail(ref, None, "Not connected to the Signa server.")

    def _delete_account(self, wipe_local: bool):
        self.wipe_local = wipe_local
        if self.client is None:
            QMessageBox.warning(self, "Delete account", "Not connected to the Signa server.")
            return
        self.client.request("delete_account", confirm="DELETE")

    def _forget_everything(self):
        """Cuenta borrada en el servidor: fuera la clave (y, si se pidió, bots y logins de IBKR de este PC)."""
        from .. import gateway
        from ..core import bots
        from . import autostart
        if self.wipe_local:
            for p in list(bots.linked()):
                bots.unlink(p)
            cfg = bots.load()
            cfg.pop("known_chats", None)
            bots.save(cfg)
            for account in gateway.ACCOUNTS:
                gateway.forget_credentials(account)
        try:
            autostart.disable()
        except OSError:
            pass
        if self.client is not None:
            self.client.stop()
            self.client = None
        net.forget_key()
        self._build()
        QMessageBox.information(self, "Signa", "Your account was deleted.")

    # --- bandeja ---------------------------------------------------------------------------------
    def _tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        t = QSystemTrayIcon(brand.icon(), self)
        t.setToolTip("Signa")
        menu = QMenu()
        menu.addAction("Open Signa", self._show)
        menu.addSeparator()
        menu.addAction("Quit Signa", self._quit)
        t.setContextMenu(menu)
        t.activated.connect(lambda r: self._show() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
                            else None)
        self.tray_menu = menu
        t.show()
        return t

    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self.quitting = True
        self.close()
        self.app.quit()

    def closeEvent(self, e):
        if self.tray is not None and not self.quitting and self.prefs.value("tray", True, type=bool):
            e.ignore()
            self.hide()
            if not self.prefs.value("tray_told", False, type=bool):
                self.tray.showMessage("Signa keeps running", "Signa is still here in the tray. Right-click it to "
                                      "quit.", QSystemTrayIcon.Information, 5000)
                self.prefs.setValue("tray_told", True)
            return
        if self.client is not None:
            self.client.stop()
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(e)
