"""Piezas comunes de la interfaz: tarjetas, filas de ajustes, interruptor, etiquetas, selector segmentado."""
from PySide6.QtCore import QByteArray, QPropertyAnimation, QRectF, QSize, Qt, Property, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                               QAbstractItemView, QHeaderView)

from . import theme

# iconos de trazo (16x16), el color se pone al pintarlos
ICONS = {
    "overview": '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/>'
                '<rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
    "providers": '<circle cx="8" cy="8" r="1.5"/><path d="M5.2 5a4.2 4.2 0 0 0 0 6M10.8 5a4.2 4.2 0 0 1 0 6'
                 'M3 3a7 7 0 0 0 0 10M13 3a7 7 0 0 1 0 10"/>',
    "trades": '<path d="M2.5 12.5l3.5-4 3 2.5 4.5-6"/><path d="M10.5 5h3v3"/>',
    "calls": '<path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z"/>',
    "mycalls": '<path d="M3 3.5h10v7H7l-3 2.5v-2.5H3z"/><path d="M8 5.2v3.6M6.2 7h3.6"/>',
    "ibkr": '<path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 0 1-7 0zM8 11.5v2"/>',
    "telegram": '<path d="M14 2.5L1.5 7.5l4.5 1.5 1.5 4.5 2-3 3.5 2.5z"/>',
    "settings": '<path d="M2.5 4.5h7M12.5 4.5h1M2.5 11.5h1M6.5 11.5h7"/><circle cx="11" cy="4.5" r="1.5"/>'
                '<circle cx="5" cy="11.5" r="1.5"/>',
    "lock": '<rect x="3.5" y="7" width="9" height="6.5" rx="1.2"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>',
    "download": '<path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10"/>',
    "stats": '<path d="M2.5 13.5h11M4.5 11V8M8 11V4.5M11.5 11V6.5"/>',
    "admin": '<path d="M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z"/>',
    "back": '<path d="M13 8H3M7.5 3.5L3 8l4.5 4.5"/>',
}


def svg_pixmap(svg: str, size: int) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)  # x2 para pantallas con escalado
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


def icon(name: str, color: str, size: int = 16) -> QIcon:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="{color}" '
           f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    return QIcon(svg_pixmap(svg, size))


def logo(size: int = 24) -> QPixmap:
    c = theme.current
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" '
           f'fill="{c["ac"]}"/><path d="M5 13h3l2.2-5 3.6 9 2.2-4H19" fill="none" stroke="{c["on"]}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    return svg_pixmap(svg, size)


def label(text: str, role: str | None = None, tone: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    if tone:
        lb.setProperty("tone", tone)
    lb.setWordWrap(wrap)
    return lb


def tag(text: str, kind: str = "good", dot: bool = False) -> QLabel:
    lb = QLabel(("● " if dot else "") + text)
    lb.setProperty("tag", kind)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lb


def provider_chip(name: str, provider_id: str) -> QLabel:
    """Chip del proveedor con SU color (ver theme.provider_color)."""
    bg, fg = theme.provider_color(provider_id)
    lb = QLabel(name)
    lb.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border-radius: 10px; padding: 3px 10px; "
                     "font-size: 9pt; font-weight: 600; }")
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lb


def chip_cell(chip: QLabel) -> QWidget:
    """Un chip dentro de una celda de tabla (alineado a la izquierda, centrado en vertical). Sin margen propio: la
    celda ya lleva el suyo (padding del estilo de la tabla) y así queda alineado con el texto de la columna."""
    return widget(hbox(chip, None))


def button(text: str, kind: str | None = None, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        b.setIcon(icon(icon_name, theme.current["tx"], 14))
    b.setCursor(Qt.PointingHandCursor)
    return b


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def widget(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


class Card(QFrame):
    """Tarjeta blanca con borde. add_row() apila filas separadas por una línea."""

    def __init__(self, padding: int = 0):
        super().__init__()
        self.setProperty("card", "true")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(0 if padding == 0 else 12)
        self._rows = 0

    def add(self, w):
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w

    def add_row(self, title: str, desc: str | None, control: QWidget | None = None, vertical: bool = False):
        row = QFrame()
        row.setProperty("row", "first" if self._rows == 0 else "true")
        self._rows += 1
        row.title = label(title, "name")
        text = vbox(row.title, *([label(desc, "small", wrap=True)] if desc else []), spacing=3)
        if vertical:
            lay = vbox(text, control, spacing=10, margins=(16, 12, 16, 12))
        else:
            lay = hbox(spacing=20, margins=(16, 12, 16, 12))
            lay.addLayout(text, 1)  # el texto se queda todo el ancho libre (y parte línea solo si hace falta)
            if control:
                lay.addWidget(control)
        row.setLayout(lay)
        self.lay.addWidget(row)
        return row


def columns(*items: QWidget, spacing: int = 18) -> QHBoxLayout:
    """Columnas pegadas arriba: cada una mide lo que su contenido, no lo que la más alta."""
    lay = hbox(*items, spacing=spacing)
    for w in items:
        lay.setAlignment(w, Qt.AlignTop)
    return lay


def section(title: str, body: QWidget) -> QWidget:
    return widget(vbox(label(title, "sec"), body, None, spacing=8))


class Toggle(QAbstractButton):
    """Interruptor on/off (como los de Windows 11 / macOS)."""

    def __init__(self, checked: bool = False, name: str = ""):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(name)
        self.setFixedSize(36, 20)
        self._pos = 1.0 if checked else 0.0
        # OJO: la propiedad NO puede llamarse "pos" (es la posición del widget: lo movía de sitio)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._pos

    def set_knob(self, v):
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def sizeHint(self):
        return QSize(36, 20)

    def paintEvent(self, _):
        c = theme.current
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["ac"] if self.isChecked() else c["line2"]))
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(3 + 16 * self._pos, 3, 14, 14))
        p.end()


class Segmented(QFrame):
    """Selector de opciones excluyentes (Contracts | Amount | % of account)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str):
        super().__init__()
        self.setObjectName("Seg")
        self.value = current
        self.changed.connect(lambda k: setattr(self, "value", k))  # 1ª conexión: ya puesto para las demás
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)  # hueco para que el botón marcado no se coma la esquina redonda
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for key, text in options:
            b = QPushButton(text)
            b.setProperty("seg", "true")
            b.setCheckable(True)
            b.setChecked(key == current)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            lay.addWidget(b)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


def kpi(title: str, value: str, sub: str, tone: str | None = None) -> Card:
    card = Card(padding=16)
    card.lay.setSpacing(4)
    card.add(label(title, "mu"))
    card.add(label(value, "kpi", tone))
    card.add(label(sub, "small"))
    return card


def table(headers: list[str], rows: list[list], right: set[int] = frozenset(), tones: dict | None = None,
          stretch: int = 1) -> QTableWidget:
    """Tabla densa en monoespaciada. Columnas a su contenido salvo `stretch`, que se queda el sobrante.
    tones = {(fila, col): 'good'|'bad'|'mu'}."""
    c = theme.current
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setFocusPolicy(Qt.NoFocus)
    t.setWordWrap(False)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.verticalHeader().setDefaultSectionSize(36)
    hh = t.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    hh.setSectionResizeMode(QHeaderView.ResizeToContents)
    hh.setSectionResizeMode(stretch, QHeaderView.Stretch)
    hh.setMinimumSectionSize(64)
    colors = {"good": c["good"], "bad": c["bad"], "mu": c["mu"]}
    for r, row in enumerate(rows):
        for col, val in enumerate(row):
            it = QTableWidgetItem(str(val))
            align = Qt.AlignRight if col in right else Qt.AlignLeft
            it.setTextAlignment(align | Qt.AlignVCenter)
            tone = (tones or {}).get((r, col))
            if tone:
                it.setForeground(QColor(colors[tone]))
            t.setItem(r, col, it)
    for col in right:
        t.horizontalHeaderItem(col).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    t.setFixedHeight(40 + 36 * len(rows) + 2)
    return t


def pnl_tone(text: str) -> str | None:
    return "good" if text.startswith("+") else "bad" if text.startswith(("−", "-")) else None
