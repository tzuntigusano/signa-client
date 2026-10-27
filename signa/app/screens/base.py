"""Esqueleto común de una pantalla: cabecera (título + controles) y cuerpo con scroll."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ..widgets import hbox, label


class Page(QWidget):
    def __init__(self, title: str, subtitle: str | None = None, right: QWidget | None = None):
        super().__init__()
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(64)
        left = [label(title, "h1")]
        if subtitle:
            left.append(label(subtitle, "mu"))
        header.setLayout(hbox(*left, None, *([right] if right else []), spacing=12, margins=(28, 0, 28, 0)))
        outer.addWidget(header)

        scroll = self.scroll_area = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 20, 28, 24)
        self.body.setSpacing(18)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    def add(self, item):
        if isinstance(item, QWidget):
            self.body.addWidget(item)
        else:
            self.body.addLayout(item)
        return item

    def finish(self):
        self.body.addStretch(1)
