"""Colores y hoja de estilos de Signa. Mismos tokens que el diseño aprobado (claro y oscuro)."""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

LIGHT = {
    "bg": "#f6f5f2", "side": "#efede8", "card": "#ffffff", "sub": "#faf9f7",
    "line": "#e4e1da", "line2": "#cfcbc2", "tx": "#1d1c1a", "mu": "#625e56",
    "ac": "#2455c3", "acbg": "#e9eefa", "on": "#ffffff",
    "good": "#17784a", "goodbg": "#e3f2e9", "bad": "#b4361f", "badbg": "#fbe8e3",
    "warn": "#8a5100", "warnbg": "#fbefd9", "chat": "#e7ebe4",
    "bid": "#1f6fd1", "ask": "#d23b2f",  # como IBKR: bid en azul, ask en rojo
}
DARK = {
    "bg": "#131518", "side": "#0e1013", "card": "#1a1d22", "sub": "#16191d",
    "line": "#2a2e35", "line2": "#3a3f48", "tx": "#e4e6e9", "mu": "#959ca6",
    "ac": "#7ea5ff", "acbg": "#1d2842", "on": "#0c1220",
    "good": "#4cbb88", "goodbg": "#142a1f", "bad": "#f2806e", "badbg": "#361b16",
    "warn": "#e6ad4d", "warnbg": "#33270f", "chat": "#0f1216",
    "bid": "#5a9cff", "ask": "#ff6f61",
}

# colores que pintan los widgets propios (interruptor, puntos de estado); se cambian con apply()
current: dict = dict(LIGHT)


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def resolve(mode: str) -> dict:
    """mode = system | light | dark."""
    if mode == "system":
        return DARK if system_is_dark() else LIGHT
    return DARK if mode == "dark" else LIGHT


def ui_font() -> QFont:
    family = "Segoe UI" if sys.platform == "win32" else ".AppleSystemUIFont" if sys.platform == "darwin" else "Sans"
    f = QFont(family)
    f.setPointSizeF(9.75)  # 13 px a 96 dpi, como en el diseño
    return f


def figures_family() -> str:
    """La fuente de los datos y las cifras: la MISMA que el resto de la app (Ivan 27/10: la monoespaciada «parecía
    IA»). Las cifras de Segoe UI tienen todas el mismo ancho, así que las columnas de números siguen cuadrando."""
    return ui_font().family()


def chevron(color: str) -> str:
    """Flecha de los desplegables: Qt solo la acepta como fichero, así que se escribe una por color."""
    path = Path(tempfile.gettempdir()) / f"signa-chevron-{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M2 3.5l3 3 3-3" '
                        f'fill="none" stroke="{color}" stroke-width="1.4" stroke-linecap="round" '
                        f'stroke-linejoin="round"/></svg>', encoding="utf-8")
    return path.as_posix()


def check(color: str) -> str:
    path = Path(tempfile.gettempdir()) / f"signa-check-{color.lstrip('#')}.svg"
    if not path.exists():
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 8.5l2.5 2.5 5.5-6" '
                        f'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
                        f'stroke-linejoin="round"/></svg>', encoding="utf-8")
    return path.as_posix()


def stylesheet(c: dict) -> str:
    mono = figures_family()  # (el nombre «mono» queda en el estilo: son las cifras)
    return f"""
* {{ color: {c['tx']}; }}
QWidget#Window, QWidget#Page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['bg']}; }}
QWidget#Sidebar {{ background: {c['side']}; border-right: 1px solid {c['line']}; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 15pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 12pt; font-weight: 600; }}
QLabel[role="sec"] {{ color: {c['mu']}; font-size: 9pt; font-weight: 600; }}
QLabel[role="mu"] {{ color: {c['mu']}; }}
QLabel[role="small"] {{ color: {c['mu']}; font-size: 9pt; }}
QLabel[role="name"] {{ font-weight: 600; }}
QLabel[role="kpi"] {{ font-family: "{mono}"; font-size: 18pt; font-weight: 500; }}
QLabel[role="mono"] {{ font-family: "{mono}"; }}
QLabel[tone="good"] {{ color: {c['good']}; }}
QLabel[tone="bad"] {{ color: {c['bad']}; }}
QLabel[tone="bid"] {{ color: {c['bid']}; }}
QLabel[tone="ask"] {{ color: {c['ask']}; }}
QFrame#Header {{ background: {c['bg']}; border-bottom: 1px solid {c['line']}; }}
QFrame[card="true"] {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 8px; }}
QFrame[row="true"] {{ background: transparent; border: 0; border-top: 1px solid {c['line']}; border-radius: 0; }}
QFrame[row="first"] {{ background: transparent; border: 0; }}
QFrame[chat="true"] {{ background: {c['chat']}; border: 1px solid {c['line']}; border-radius: 8px; }}
QLabel[tag="good"] {{ background: {c['goodbg']}; color: {c['good']}; border-radius: 10px; padding: 3px 10px; font-size: 9pt; }}
QLabel[tag="warn"] {{ background: {c['warnbg']}; color: {c['warn']}; border-radius: 10px; padding: 3px 10px; font-size: 9pt; }}
QLabel[tag="bad"] {{ background: {c['badbg']}; color: {c['bad']}; border-radius: 10px; padding: 3px 10px; font-size: 9pt; }}
QLabel[tag="accent"] {{ background: {c['acbg']}; color: {c['ac']}; border-radius: 10px; padding: 3px 10px; font-size: 9pt; }}
QLabel[tag="neutral"] {{ background: {c['sub']}; color: {c['mu']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 2px 9px; font-size: 9pt; }}
QPushButton {{ background: {c['card']}; border: 1px solid {c['line2']}; border-radius: 6px; padding: 0 14px; min-height: 30px; }}
QPushButton:hover {{ border-color: {c['mu']}; }}
QPushButton[kind="primary"] {{ background: {c['ac']}; border-color: {c['ac']}; color: {c['on']}; font-weight: 600; }}
QPushButton[kind="link"] {{ background: transparent; border: 0; padding: 0; min-height: 0; color: {c['ac']}; font-size: 9pt; text-decoration: underline; }}
QPushButton:disabled {{ background: {c['sub']}; border-color: {c['line']}; color: {c['mu']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['acbg']}; border-color: {c['acbg']}; color: {c['mu']}; font-weight: 600; }}
QLabel[tone="warn"] {{ color: {c['warn']}; }}
QPushButton[kind="danger"] {{ color: {c['bad']}; border-color: {c['bad']}; }}
QPushButton[kind="key"] {{ background: {c['acbg']}; border: 0; color: {c['ac']}; font-weight: 600; font-size: 9pt; }}
QPushButton[kind="sellrow"] {{ background: {c['bad']}; border: 0; color: {c['on']}; font-weight: 600; font-size: 9pt; }}
QPushButton[kind="opt"] {{ background: {c['sub']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 0 4px; min-height: 32px; font-family: "{mono}"; font-size: 9pt; }}
QPushButton[kind="opt"]:hover {{ background: {c['acbg']}; border-color: {c['ac']}; color: {c['ac']}; }}
QPushButton[kind="opt"]:checked {{ background: {c['acbg']}; border-color: {c['ac']}; color: {c['ac']}; font-weight: 600; }}
QPushButton[kind="sell"] {{ background: {c['bad']}; border-color: {c['bad']}; color: {c['on']}; font-weight: 600; min-height: 38px; }}
QPushButton[kind="sell"]:disabled, QPushButton[kind="buy"]:disabled {{ background: {c['sub']}; border-color: {c['line']}; color: {c['mu']}; }}
QPushButton[kind="buy"] {{ background: {c['good']}; border-color: {c['good']}; color: {c['on']}; font-weight: 600; min-height: 38px; }}
QPushButton[kind="quiet"] {{ background: transparent; border: 0; padding: 0 2px; min-height: 0; color: {c['mu']}; font-size: 9pt; }}
QPushButton[kind="quiet"]:hover {{ color: {c['bad']}; }}
QPushButton[kind="step"] {{ background: {c['sub']}; border: 1px solid {c['line']}; padding: 0; min-width: 32px; max-width: 32px; min-height: 32px; font-size: 12pt; }}
QLabel[role="stepval"] {{ font-family: "{mono}"; font-size: 11pt; font-weight: 600; min-width: 40px; qproperty-alignment: AlignCenter; }}
QFrame#DrawerCard {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 10px; }}
QPushButton#Nav {{ background: transparent; border: 0; border-radius: 6px; text-align: left; padding: 0 10px; min-height: 34px; color: {c['mu']}; }}
QPushButton#Nav:hover {{ background: {c['card']}; color: {c['tx']}; }}
QPushButton#Nav:checked {{ background: {c['card']}; color: {c['tx']}; font-weight: 600; border: 1px solid {c['line']}; }}
QPushButton[seg="true"] {{ border: 0; border-radius: 4px; background: transparent; color: {c['mu']}; padding: 0 12px; min-height: 26px; }}
QPushButton[seg="true"]:checked {{ background: {c['acbg']}; color: {c['ac']}; }}
QFrame#Seg {{ border: 1px solid {c['line2']}; border-radius: 6px; background: {c['card']}; }}
QLineEdit, QComboBox {{ background: {c['card']}; border: 1px solid {c['line2']}; border-radius: 6px; padding: 0 10px; min-height: 30px; selection-background-color: {c['acbg']}; selection-color: {c['tx']}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {c['ac']}; }}
QLineEdit[mono="true"] {{ font-family: "{mono}"; }}
QComboBox::drop-down {{ border: 0; width: 26px; }}
QComboBox::down-arrow {{ image: url("{chevron(c['mu'])}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['line2']}; selection-background-color: {c['acbg']}; selection-color: {c['tx']}; }}
QCheckBox {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['line2']}; border-radius: 4px; background: {c['card']}; }}
QCheckBox::indicator:checked {{ background: {c['ac']}; border-color: {c['ac']}; image: url("{check(c['on'])}"); }}
QTableWidget {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 8px; gridline-color: transparent; font-family: "{mono}"; alternate-background-color: {c['card']}; }}
QTableWidget::item {{ border-bottom: 1px solid {c['line']}; padding: 0 12px; }}
QTableWidget::item:selected {{ background: {c['acbg']}; color: {c['tx']}; }}
QHeaderView::section {{ background: {c['card']}; color: {c['mu']}; border: 0; border-bottom: 1px solid {c['line']}; padding: 8px 12px; font-weight: 500; font-size: 9pt; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['line2']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {c['card']}; color: {c['tx']}; border: 1px solid {c['line2']}; }}
"""


def apply(app, mode: str) -> dict:
    """Aplica el tema a toda la app y devuelve sus colores."""
    c = resolve(mode)
    current.clear()
    current.update(c)
    pal = app.palette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "card"), (QPalette.Text, "tx"),
                      (QPalette.WindowText, "tx"), (QPalette.Button, "card"), (QPalette.ButtonText, "tx"),
                      (QPalette.Highlight, "ac"), (QPalette.HighlightedText, "on"),
                      (QPalette.PlaceholderText, "mu"), (QPalette.Link, "ac")):
        pal.setColor(role, QColor(c[key]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(c))
    return c


# Colores de PROVEEDOR (chips): ninguno se parece a los de estado/tipo (verde, ámbar, rojo, azul, gris) y todos se
# leen bien en claro y en oscuro. A cada proveedor le toca uno AL AZAR (sin repetir mientras queden libres) y se
# recuerda, para que siempre tenga el mismo.  (fondo claro, texto claro, fondo oscuro, texto oscuro)
PROVIDER_PALETTE = {
    "violet": ("#ede9fe", "#5b21b6", "#2e1f4f", "#c4b5fd"),
    "fuchsia": ("#fae8ff", "#86198f", "#3b1240", "#f0abfc"),
    "cyan": ("#cffafe", "#155e75", "#0b3340", "#67e8f9"),
    "sienna": ("#f6e7de", "#7c3a1d", "#3a2418", "#e9b99c"),
    "khaki": ("#f1f0d6", "#57550f", "#2f2e14", "#dcd88a"),
}


def provider_color(provider_id: str) -> tuple[str, str]:
    """(fondo, texto) del chip de ese proveedor en el tema actual."""
    import json
    import random

    from PySide6.QtCore import QSettings
    prefs = QSettings("Signa", "Signa")
    try:
        chosen = json.loads(prefs.value("provider_colors", "{}"))
    except (TypeError, ValueError):
        chosen = {}
    name = chosen.get(provider_id)
    if name not in PROVIDER_PALETTE:
        free = [n for n in PROVIDER_PALETTE if n not in chosen.values()] or list(PROVIDER_PALETTE)
        name = chosen[provider_id] = random.choice(free)
        prefs.setValue("provider_colors", json.dumps(chosen))
    lbg, lfg, dbg, dfg = PROVIDER_PALETTE[name]
    return (dbg, dfg) if current.get("bg") == DARK["bg"] else (lbg, lfg)
