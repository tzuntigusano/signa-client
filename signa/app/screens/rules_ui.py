"""Providers › las REGLAS EDITABLES de la estrategia (Ivan 09/11/2026), anidadas bajo su interruptor principal: cada
una con su casilla y sus números dentro de la frase. Lo que cambias se ve en azul con «Signa: … ↺ Reset». El «Max
chase» de la columna de la derecha es la MISMA regla que «Don't chase» de Auto buy: cambiar uno cambia el otro.

Los valores y la validación son los de signa/core/rules.py (los mismos que usa el servidor)."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QLayout, QLineEdit, QSizePolicy, QWidget

from signa.core import rules

from ..widgets import button, hbox, label, vbox, widget

# id -> (título, frase con {campo} donde va cada casilla, unidad de cada campo)
TEXT = {
    "chase": ("Don't chase", "Skip it if the ask is more than {lo} over his price under $2, or more than {hi} from $2.",
              {"lo": "%", "hi": "%"}),
    "mismatch": ("Wrong-contract guard", "Skip it if the ask is {below} below or {above} above his price: it is "
                 "probably another expiry or strike.", {"below": "%", "above": "%"}),
    "doubt": ("Doubtful wording", "If he enters with doubts (“lotto”, “size light”…), buy only {n} contract(s).",
              {"n": ""}),
    "warn": ("His warning, while winning", "Before +{ref}, raise the stop to entry −{sl}. Losing, his warning is "
             "ignored and the stop decides.", {"ref": "%", "sl": "%"}),
    "tp1": ("Partial take profit", "At +{pct} sell {frac} of the contracts once. The rest gets its stop at "
            "break-even.", {"pct": "%", "frac": ""}),
    "double": ("Early double", "If it reaches +{pct} before the time check, the stop trails {trail} below the high.",
               {"pct": "%", "trail": "%"}),
    "time": ("Time check", "At minute {hold}: out if it is under +{floor}. Above it, the stop trails {trail} below "
             "the high, never under the floor.", {"hold": "", "floor": "%", "trail": "%"}),
    "eod": ("End of day", "Everything out at {at} ET, expiring today or not. Unticked: only what expires today goes "
            "at 15:55.", {"at": ""}),
}
OFF_NOTE = {
    "auto_buy": "Off: you get a card to buy. The ticked rules still say whether the card recommends it.",
    "auto_protect": "Off: his warnings send you a card with the stop it would set.",
    "auto_rules": "Off: only your initial stop is placed. Each rule below sends you a card when it would act.",
}


class FlowLayout(QLayout):
    """Como un párrafo: los trozos de la frase y sus casillas en línea, partiendo de línea donde haga falta."""

    def __init__(self, parent=None, hspacing: int = 4, vspacing: int = 4):
        super().__init__(parent)
        self.items, self.hs, self.vs = [], hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 (Qt)
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):  # noqa: N802
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):  # noqa: N802
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for it in self.items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line > 0:
                x, y, line = rect.x(), y + line + self.vs, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y + max(0, (line or hint.height()) - hint.height()) // 2), hint))
            x += hint.width() + self.hs
            line = max(line, hint.height())
        return y + line - rect.y()


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


class RulesEditor:
    """Los datos (prov['rules_ui']: las reglas completas, con lo que escribas tal cual) y sus casillas. on_change()
    avisa a la pantalla para «Unsaved changes»."""

    def __init__(self, prov: dict, on_change):
        self.prov, self.on_change = prov, on_change
        self.fields: dict[tuple[str, str], list[QLineEdit]] = {}
        self.checks: dict[str, list[QCheckBox]] = {}
        self.resets: dict[str, list[QWidget]] = {}
        self.texts: dict[str, list[QWidget]] = {}

    @property
    def data(self) -> dict:
        return self.prov["rules_ui"]

    # --- piezas ------------------------------------------------------------------------------------------------
    def group(self, group_id: str, parent_on: bool) -> QWidget:
        """Las reglas de un interruptor, metidas hacia dentro bajo él."""
        box = QFrame()
        box.setProperty("kids", "true")
        lay = vbox(spacing=2, margins=(14, 0, 0, 0))
        if not parent_on:
            lay.addWidget(label(OFF_NOTE[group_id], "offnote", wrap=True))
        for rid, r in rules.SCHEMA.items():
            if r["group"] == group_id:
                lay.addWidget(self._rule(rid))
        box.setLayout(lay)
        return widget(vbox(box, margins=(34, 0, 16, 14)))

    def _rule(self, rid: str) -> QWidget:
        title, sentence, units = TEXT[rid]
        check = QCheckBox(title)
        check.setProperty("role", "name")
        check.setChecked(bool(self.data[rid]["on"]))
        check.toggled.connect(lambda on, rid=rid: self._set_on(rid, on))
        self.checks.setdefault(rid, []).append(check)
        signa = label("", "small")
        reset = button("↺ Reset", "reset")
        reset.setToolTip(f"Put Signa's values back in {title}")
        reset.clicked.connect(lambda _=False, rid=rid: self._reset(rid))
        self.resets.setdefault(rid, []).extend([signa, reset])
        head = hbox(check, signa, reset, None, spacing=8)
        text = QWidget()
        flow = FlowLayout(text)
        unit = None  # la unidad del último campo: la puntuación que va pegada detrás («%,» «%.») se le añade
        for i, chunk in enumerate(sentence.replace("}", "{").split("{")):
            if i % 2:  # un campo
                box, unit = self._field(rid, chunk, units[chunk])
                flow.addWidget(box)
                continue
            words = chunk.split()
            if unit is not None and words and not chunk[:1].isspace() and words[0][:1] in ",.:;)":
                unit.setText(unit.text() + words.pop(0))
                unit.setVisible(True)
            for word in words:
                flow.addWidget(label(word, "rule"))
            unit = None
        self.texts.setdefault(rid, []).append(text)
        self._paint(rid)
        return widget(vbox(head, widget(vbox(text, margins=(26, 0, 0, 0))), spacing=2, margins=(0, 8, 0, 6)))

    def _field(self, rid: str, key: str, unit: str) -> tuple[QWidget, QLabel]:
        f = QLineEdit(_fmt(self.data[rid][key]))
        f.setProperty("mono", "true")
        f.setProperty("rule", "true")
        f.setAlignment(Qt.AlignRight)
        f.setFixedWidth(60 if key in ("at", "frac") else 52)
        f.setAccessibleName(f"{TEXT[rid][0]} · {key}")
        f.textEdited.connect(lambda t, rid=rid, key=key, f=f: self._set(rid, key, t, f))
        self.fields.setdefault((rid, key), []).append(f)
        u = label(unit, "rule")
        u.setVisible(bool(unit))  # sin unidad, sin hueco (salvo que se le pegue puntuación)
        w = widget(hbox(f, u, spacing=2))
        w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        return w, u

    def chase_row(self) -> QWidget:
        """«Max chase» de la columna de la derecha: la misma regla que «Don't chase» (para quien no tiene Auto buy)."""
        check = QCheckBox("Max chase")
        check.setProperty("role", "name")
        check.setChecked(bool(self.data["chase"]["on"]))
        check.toggled.connect(lambda on: self._set_on("chase", on))
        self.checks.setdefault("chase", []).append(check)
        reset = button("↺ Reset", "reset")
        reset.clicked.connect(lambda _=False: self._reset("chase"))
        self.resets.setdefault("chase", []).append(reset)
        row = hbox(None, label("under $2", "small"), self._field("chase", "lo", "%")[0], label("· from $2", "small"),
                   self._field("chase", "hi", "%")[0], spacing=4)
        self._paint("chase")
        return widget(vbox(hbox(check, None, reset),
                           label("Skip if the ask is this much over his price · also in Auto buy › Don't chase",
                                 "small", wrap=True),
                           row, spacing=4, margins=(16, 12, 16, 12)))

    # --- cambios -------------------------------------------------------------------------------------------------
    def _set(self, rid: str, key: str, text: str, source: QLineEdit):
        self.data[rid][key] = text.strip()
        for f in self.fields[(rid, key)]:
            if f is not source:
                f.setText(text)
        self._paint(rid)
        self.on_change()

    def _set_on(self, rid: str, on: bool):
        self.data[rid]["on"] = on
        for c in self.checks[rid]:
            if c.isChecked() != on:
                c.blockSignals(True)
                c.setChecked(on)
                c.blockSignals(False)
        self._paint(rid)
        self.on_change()

    def _reset(self, rid: str):
        for key, (default, *_rest) in rules.SCHEMA[rid]["fields"].items():
            self.data[rid][key] = default
            for f in self.fields.get((rid, key), []):
                f.setText(_fmt(default))
        self._paint(rid)
        self.on_change()

    def _paint(self, rid: str):
        """Lo tuyo en azul, «Signa: …» y ↺ Reset si cambiaste algo; desmarcada, sus casillas apagadas."""
        on = bool(self.data[rid]["on"])
        changed = []
        for key, (default, *_rest) in rules.SCHEMA[rid]["fields"].items():
            mine = self._differs(rid, key, self.data[rid][key], default)
            changed.append(mine)
            for f in self.fields.get((rid, key), []):
                f.setEnabled(on)
                f.setProperty("changed", "true" if mine else "false")
                f.style().unpolish(f)
                f.style().polish(f)
        defaults = " · ".join(_fmt(f[0]) + (" %" if f[1] == "pct" else "")
                              for f in rules.SCHEMA[rid]["fields"].values())
        for w in self.resets.get(rid, []):
            w.setVisible(any(changed))
            if isinstance(w, QLabel):
                w.setText(f"Signa: {defaults}")
        for t in self.texts.get(rid, []):
            for lb in t.findChildren(QLabel):
                lb.setEnabled(on)

    @staticmethod
    def _differs(rid: str, key: str, value, default) -> bool:
        try:
            return rules.normalize({rid: {key: value}}) != {}
        except rules.RuleError:
            return str(value) != _fmt(default)

    # --- lo que se guarda ----------------------------------------------------------------------------------------
    def stored(self) -> dict:
        """Lo que se manda al servidor (solo lo distinto de Signa). RuleError si hay algo mal escrito."""
        return rules.normalize(self.data)


def ui_rules(p: dict) -> dict:
    """Las reglas completas para la pantalla: las de Signa con las tuyas encima (y tu «Max chase» de antes)."""
    d = (p.get("defaults") or {}).get("chase")
    return rules.effective(p.get("rules") or {}, p.get("chase_pct"), d)
