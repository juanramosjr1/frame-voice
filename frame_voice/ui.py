"""The window: fuelCell intro, big mic button, edit keys and settings."""

import math
import os
import sys
import threading
import time

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPointF, QProcess, QRectF, QSize,
                            Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QIcon,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QCheckBox,
                               QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from . import config as config_mod
from . import focus
from .app import ERROR, LISTENING, LOADING, READY, WORKING
from .log import log
from .vr import BUTTON_LABELS, BUTTONS, PRESETS, SteamVR, preset_hint

# -- look -------------------------------------------------------------------
BG = "#0d1016"
CARD = "#161a22"
CARD_HOVER = "#1d2330"
CARD_PRESS = "#252c3b"
BORDER = "#232a37"
TEXT = "#eef1f6"
MUTED = "#8b94a7"
ACCENT = "#2ee6b8"       # fuel-cell green
ACCENT_2 = "#29a8ff"     # hydrogen blue
RED = "#ff4d5e"
AMBER = "#ffb547"
FONT_FAMILIES = ["Inter", "Noto Sans", "Cantarell", "DejaVu Sans", "Sans Serif"]

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; }}
QLabel {{ background: transparent; }}
#muted {{ color: {MUTED}; }}
#status {{ font-size: 22px; font-weight: 600; }}
#hint {{ color: {MUTED}; font-size: 15px; }}
#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px; }}
#cardTitle {{ color: {MUTED}; font-size: 12px; font-weight: 700; letter-spacing: 1.5px; }}
#lastText {{ font-size: 17px; }}
QToolButton#key {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px;
    color: {TEXT}; font-size: 15px; font-weight: 600; padding: 12px 4px 10px 4px;
}}
QToolButton#key:hover {{ background: {CARD_HOVER}; border-color: #2f3849; }}
QToolButton#key:pressed {{ background: {CARD_PRESS}; border-color: {ACCENT}; }}
QToolButton#key:disabled {{ color: #4b5263; }}
QPushButton#small {{
    background: {CARD_HOVER}; border: 1px solid {BORDER}; border-radius: 10px;
    color: {TEXT}; font-size: 14px; font-weight: 600; padding: 8px 14px;
}}
QPushButton#small:hover {{ border-color: {ACCENT}; }}
QPushButton#small:disabled {{ color: #4b5263; }}
QToolButton#gear {{ background: transparent; border: none; border-radius: 20px; }}
QToolButton#gear:hover {{ background: {CARD}; }}
QPushButton#seg {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px;
    color: {TEXT}; font-size: 15px; font-weight: 600; padding: 12px 10px;
}}
QPushButton#seg:checked {{ background: #12322c; border: 2px solid {ACCENT}; }}
QPushButton#primary {{
    background: {ACCENT}; color: #04140f; border: none; border-radius: 12px;
    font-size: 16px; font-weight: 700; padding: 12px 24px;
}}
QCheckBox {{ font-size: 16px; spacing: 12px; padding: 6px 0; background: transparent; }}
QCheckBox::indicator {{ width: 44px; height: 26px; border-radius: 13px;
    background: #2a3140; border: 1px solid {BORDER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QPlainTextEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
    font-family: monospace; font-size: 13px; }}
"""


def font(px, weight=QFont.Normal, spacing=0.0):
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def ease_out(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def ease_out_back(p):
    p = max(0.0, min(1.0, p))
    c = 1.4
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2


def accent_gradient(x1, y1, x2, y2):
    g = QLinearGradient(x1, y1, x2, y2)
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor(ACCENT_2))
    return g


# -- icons --------------------------------------------------------------------
def draw_icon(name, size=30, color=TEXT):
    """Simple line icons drawn in code, so nothing depends on installed fonts."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 24.0
    p.scale(s, s)
    pen = QPen(QColor(color), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if name == "copy":
        p.drawRoundedRect(QRectF(8, 8, 12, 12), 2.5, 2.5)
        path.moveTo(5, 15)
        path.lineTo(5, 6.5)
        path.quadTo(5, 4, 7.5, 4)
        path.lineTo(15, 4)
    elif name == "paste":
        p.drawRoundedRect(QRectF(5, 5, 14, 16), 2.5, 2.5)
        p.drawRoundedRect(QRectF(9, 3, 6, 4), 1.5, 1.5)
        path.moveTo(9, 12)
        path.lineTo(15, 12)
        path.moveTo(9, 16)
        path.lineTo(13, 16)
    elif name == "cut":
        p.drawEllipse(QPointF(7, 17.5), 2.8, 2.8)
        p.drawEllipse(QPointF(17, 17.5), 2.8, 2.8)
        path.moveTo(9, 15.5)
        path.lineTo(18, 4)
        path.moveTo(15, 15.5)
        path.lineTo(6, 4)
    elif name == "select_all":
        dash = QPen(pen)
        dash.setDashPattern([2.0, 1.6])
        p.setPen(dash)
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
        p.setPen(pen)
        path.moveTo(7.5, 10)
        path.lineTo(16.5, 10)
        path.moveTo(7.5, 14)
        path.lineTo(13.5, 14)
    elif name == "undo":
        path.moveTo(8, 5)
        path.lineTo(4, 9)
        path.lineTo(8, 13)
        path.moveTo(4, 9)
        path.lineTo(14, 9)
        path.cubicTo(22, 9, 22, 20, 14, 20)
        path.lineTo(9, 20)
    elif name == "backspace":
        path.moveTo(9, 5)
        path.lineTo(20, 5)
        path.quadTo(21.5, 5, 21.5, 6.5)
        path.lineTo(21.5, 17.5)
        path.quadTo(21.5, 19, 20, 19)
        path.lineTo(9, 19)
        path.lineTo(2.5, 12)
        path.closeSubpath()
        path.moveTo(12, 9)
        path.lineTo(17, 15)
        path.moveTo(17, 9)
        path.lineTo(12, 15)
    elif name == "space":
        path.moveTo(3, 11)
        path.lineTo(3, 16)
        path.lineTo(21, 16)
        path.lineTo(21, 11)
    elif name == "enter":
        path.moveTo(20, 5)
        path.lineTo(20, 13)
        path.quadTo(20, 15, 18, 15)
        path.lineTo(5, 15)
        path.moveTo(9, 11)
        path.lineTo(5, 15)
        path.lineTo(9, 19)
    elif name == "gear":
        p.drawEllipse(QPointF(12, 12), 3.0, 3.0)
        pts = []
        for i in range(8):
            a = i * 45
            for da, r in ((-15, 7.2), (-8, 9.8), (8, 9.8), (15, 7.2)):
                rad = math.radians(a + da)
                pts.append(QPointF(12 + r * math.cos(rad), 12 + r * math.sin(rad)))
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def app_icon():
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(BG))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
    p.setBrush(accent_gradient(48, 48, 208, 208))
    p.drawEllipse(QRectF(48, 48, 160, 160))
    draw_mic(p, QPointF(128, 128), 1.0, QColor("#04140f"))
    p.end()
    return QIcon(pm)


def draw_mic(p, c, scale, color):
    """Microphone glyph centred on c."""
    p.save()
    p.translate(c)
    p.scale(scale, scale)
    pen = QPen(color, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(-13, -38, 26, 46), 13, 13)
    p.setBrush(Qt.NoBrush)
    p.setPen(pen)
    arc = QPainterPath()
    arc.moveTo(-24, -6)
    arc.cubicTo(-24, 26, 24, 26, 24, -6)
    p.drawPath(arc)
    p.drawLine(QPointF(0, 19), QPointF(0, 32))
    p.drawLine(QPointF(-12, 32), QPointF(12, 32))
    p.restore()


# -- intro ------------------------------------------------------------------------
class Intro(QWidget):
    """fuelCell intro: letters rise in, an energy line charges, then it fades out."""

    finished = Signal()
    DURATION = 1550

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(620, 340)
        self.t = 0.0
        self._done = False
        self.anim = QVariantAnimation(self, startValue=0.0, endValue=float(self.DURATION),
                                      duration=self.DURATION)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self._finish)
        self.word_font = font(78, QFont.Bold, -1.5)
        self.sub_font = font(14, QFont.DemiBold, 6.0)

    def start(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())
        self.show()
        self.anim.start()

    def mousePressEvent(self, _event):  # click to skip
        self.anim.stop()
        self._finish()

    def _tick(self, value):
        self.t = value
        self.update()

    def _finish(self):
        if not self._done:
            self._done = True
            self.finished.emit()
            self.close()

    def paintEvent(self, _event):
        t = self.t
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(BG))

        # soft glow that "powers up" behind the word
        glow = ease_out(t / 700)
        g = QRadialGradient(QPointF(w / 2, h / 2 - 10), 260)
        c1 = QColor(ACCENT)
        c1.setAlphaF(0.20 * glow)
        c2 = QColor(ACCENT_2)
        c2.setAlphaF(0.06 * glow)
        g.setColorAt(0, c1)
        g.setColorAt(0.55, c2)
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g)

        # wordmark, one letter at a time
        word = "fuelCell"
        fm = QFontMetricsF(self.word_font)
        total = sum(fm.horizontalAdvance(ch) for ch in word)
        x = (w - total) / 2
        base = h / 2 + fm.ascent() / 2 - 14
        p.setFont(self.word_font)
        for i, ch in enumerate(word):
            adv = fm.horizontalAdvance(ch)
            k = ease_out_back((t - 80 - i * 55) / 430)
            alpha = ease_out((t - 80 - i * 55) / 300)
            if alpha > 0:
                p.save()
                p.setOpacity(alpha)
                p.translate(x + adv / 2, base + (1 - k) * 34)
                p.scale(0.85 + 0.15 * k, 0.85 + 0.15 * k)
                if ch == "C":
                    p.setPen(QPen(accent_gradient(-adv / 2, -fm.ascent(), adv / 2, 0), 1))
                else:
                    p.setPen(QColor(TEXT))
                p.drawText(QPointF(-adv / 2, 0), ch)
                p.restore()
            x += adv

        # energy line charging outwards from the centre, with a spark
        line_p = ease_out((t - 600) / 380)
        if line_p > 0:
            y = base + 26
            half = total / 2 * line_p
            grad = accent_gradient(w / 2 - half, y, w / 2 + half, y)
            p.setPen(QPen(grad, 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(w / 2 - half, y), QPointF(w / 2 + half, y))
            spark = QRadialGradient(QPointF(w / 2 + half, y), 14)
            spark.setColorAt(0, QColor(255, 255, 255, int(220 * (1 - line_p * 0.6))))
            spark.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(spark)
            p.drawEllipse(QPointF(w / 2 + half, y), 14, 14)

        # subtitle
        sub_a = ease_out((t - 820) / 300)
        if sub_a > 0:
            p.setOpacity(sub_a)
            p.setFont(self.sub_font)
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, base + 42 + (1 - sub_a) * 8, w, 24), Qt.AlignHCenter,
                       "VOICE TYPING")
            p.setOpacity(1)

        # fade out
        out = ease_out((t - 1280) / 270)
        if out > 0:
            veil = QColor(BG)
            veil.setAlphaF(out)
            p.fillRect(self.rect(), veil)
        p.end()


# -- mic button ---------------------------------------------------------------------
class MicButton(QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setFixedSize(220, 220)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.state = LOADING
        self.phase = 0.0
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.hover = 0.0

    def set_state(self, state):
        self.state = state
        self.update()

    def enterEvent(self, e):
        self.hover = 1.0
        self.update()

    def leaveEvent(self, e):
        self.hover = 0.0
        self.update()

    def _tick(self):
        if self.state in (LISTENING, WORKING, LOADING):
            self.phase = (self.phase + 0.016) % 1000
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = 74.0
        state = self.state

        if state == LISTENING:
            # ripples
            for k in range(3):
                f = (self.phase / 1.6 + k / 3) % 1
                col = QColor(RED)
                col.setAlphaF(0.45 * (1 - f))
                p.setPen(QPen(col, 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r + 6 + f * 32, r + 6 + f * 32)
            top, bottom = QColor("#ff6b78"), QColor("#e5283c")
        elif state == WORKING:
            top, bottom = QColor("#ffc670"), QColor("#f08c1a")
        elif state in (LOADING, ERROR):
            top, bottom = QColor("#3a4253"), QColor("#2a303d")
        else:
            glow = QRadialGradient(c, r + 30)
            gc = QColor(ACCENT)
            gc.setAlphaF(0.22 + 0.10 * self.hover)
            glow.setColorAt(0.6, gc)
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, r + 30, r + 30)
            top, bottom = QColor(ACCENT), QColor(ACCENT_2)
            if self.isDown():
                top, bottom = top.darker(115), bottom.darker(115)

        g = QLinearGradient(c.x() - r, c.y() - r, c.x() + r, c.y() + r)
        g.setColorAt(0, top)
        g.setColorAt(1, bottom)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, r, r)

        if state in (WORKING, LOADING):
            # spinner arc
            p.setPen(QPen(QColor(255, 255, 255, 200), 4, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            start = int(-self.phase * 360 * 16 * 1.2) % (360 * 16)
            p.drawArc(QRectF(c.x() - r - 12, c.y() - r - 12, 2 * r + 24, 2 * r + 24),
                      start, 100 * 16)

        glyph = QColor("#04140f") if state == READY else QColor("white")
        if state == LISTENING:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("white"))
            p.drawRoundedRect(QRectF(c.x() - 20, c.y() - 20, 40, 40), 9, 9)
        else:
            if state in (LOADING, ERROR):
                glyph.setAlphaF(0.55)
            draw_mic(p, QPointF(c.x(), c.y() + 2), 0.95, glyph)
        p.end()


# -- settings -------------------------------------------------------------------
class Settings(QDialog):
    def __init__(self, parent, cfg, engine, vr=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.vr = vr
        self._proc = None  # the system check, while it runs
        self.setWindowTitle("Settings")
        self.setMinimumWidth(860)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.setSizeConstraint(QVBoxLayout.SetFixedSize)

        title = QLabel("Settings")
        title.setFont(font(24, QFont.Bold))
        lay.addWidget(title)

        # two columns, so it fits the Frame's desktop without scrolling
        cols = QHBoxLayout()
        cols.setSpacing(28)
        left, right = QVBoxLayout(), QVBoxLayout()
        for col in (left, right):
            col.setSpacing(10)
            cols.addLayout(col)
        lay.addLayout(cols)

        left.addWidget(self._section("SPEECH MODEL"))
        seg = QHBoxLayout()
        self.models = QButtonGroup(self)
        for name, label in config_mod.MODELS.items():
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(name == cfg["model"])
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, n=name: self._model(n))
            self.models.addButton(b)
            seg.addWidget(b)
        left.addLayout(seg)
        note = QLabel("Fast is quickest. Most accurate is slower and downloads a bigger model.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        left.addWidget(note)

        left.addWidget(self._section("TYPING"))
        self._toggle(left, "Add a space after each dictation", "add_space")
        self._toggle(left, "Press Enter after each dictation", "press_enter")

        left.addWidget(self._section("APP"))
        self._toggle(left, "Show the fuelCell intro", "show_intro")
        self._toggle(left, "Start automatically with SteamVR", "autostart", self._autostart)

        right.addWidget(self._section("CONTROLLER SHORTCUTS"))
        seg2 = QHBoxLayout()
        self.presets = QButtonGroup(self)
        for key, (label, _hint) in PRESETS.items():
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(key == cfg["controller_preset"])
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._preset(k))
            self.presets.addButton(b)
            seg2.addWidget(b)
        right.addLayout(seg2)
        self.preset_hint = QLabel()
        self.preset_hint.setObjectName("hint")
        self.preset_hint.setWordWrap(True)
        right.addWidget(self.preset_hint)
        self._toggle(right, "Copy and paste shortcuts (Trigger + A, Y, X)", "edit_shortcuts",
                     self._options)
        self._toggle(right, "Keep shortcuts on during VR games", "in_games", self._options)
        custom = QPushButton("Customize buttons in SteamVR")
        custom.setObjectName("small")
        custom.clicked.connect(self._customize)
        right.addWidget(custom, 0, Qt.AlignLeft)
        self._update_hint()

        # live controller test: press buttons and watch them light up
        test = QFrame()
        test.setObjectName("card")
        tl = QVBoxLayout(test)
        tl.setContentsMargins(14, 10, 14, 12)
        tl.setSpacing(8)
        tt = QLabel("TEST YOUR BUTTONS")
        tt.setObjectName("cardTitle")
        tl.addWidget(tt)
        self.vr_line = QLabel("")
        self.vr_line.setObjectName("hint")
        self.vr_line.setWordWrap(True)
        tl.addWidget(self.vr_line)
        self.turn_on = QPushButton("Turn on")
        self.turn_on.setObjectName("primary")
        self.turn_on.clicked.connect(self._turn_on)
        self.turn_on.hide()
        tl.addWidget(self.turn_on, 0, Qt.AlignLeft)
        # buttons SteamVR reports, then what they add up to
        self.pills = {}
        for row in ([(f"btn:{b}", BUTTON_LABELS[b]) for b in BUTTONS],
                    [("talk", "Talk"), ("copy", "Copy"), ("paste", "Paste"),
                     ("select_all", "Select all")]):
            pills = QHBoxLayout()
            for key, label in row:
                pill = QLabel(label)
                pill.setAlignment(Qt.AlignCenter)
                pill.setMinimumHeight(34)
                self.pills[key] = pill
                pills.addWidget(pill)
            tl.addLayout(pills)
        note = QLabel("While this is open, buttons only light up here.")
        note.setObjectName("hint")
        tl.addWidget(note)
        right.addWidget(test)
        self._refresh_test()
        self._test_timer = QTimer(self, interval=100)
        self._test_timer.timeout.connect(self._refresh_test)
        self._test_timer.start()

        left.addStretch()
        right.addStretch()

        row = QHBoxLayout()
        check_btn = QPushButton("Run system check")
        check_btn.setObjectName("small")
        check_btn.clicked.connect(self._check)
        row.addWidget(check_btn)
        quit_btn = QPushButton("Quit app")
        quit_btn.setObjectName("small")
        quit_btn.clicked.connect(QApplication.quit)
        row.addWidget(quit_btn)
        row.addStretch()
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        lay.addSpacing(6)
        lay.addLayout(row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(170)
        self.output.hide()
        left.insertWidget(left.count() - 1, self.output)  # the left column has room

        # Nothing here takes keyboard focus, so stray typed keys can't press
        # a button (dictation typed into this window must never "click" Quit).
        for w in self.findChildren(QWidget):
            w.setFocusPolicy(Qt.NoFocus)

    def _section(self, text):
        label = QLabel(text)
        label.setObjectName("cardTitle")
        return label

    def _toggle(self, lay, text, key, then=None):
        box = QCheckBox(text)
        box.setFocusPolicy(Qt.NoFocus)
        box.setChecked(bool(self.cfg.get(key)))
        box.toggled.connect(lambda on: (self._set(key, on), then and then()))
        lay.addWidget(box)

    def _set(self, key, value):
        self.cfg[key] = value
        config_mod.save(self.cfg)

    def _model(self, name):
        if name != self.cfg["model"]:
            self.engine.reload_model(name)
            config_mod.save(self.cfg)

    def _update_hint(self):
        self.preset_hint.setText(preset_hint(self.cfg["controller_preset"],
                                             self.cfg["edit_shortcuts"]))

    def _preset(self, key):
        self._set("controller_preset", key)
        if self.vr:
            self.vr.set_preset(key)
        self._update_hint()
        self.parent().update_controller_hint()

    def _options(self):
        if self.vr:
            self.vr.set_options(edits=self.cfg["edit_shortcuts"], in_games=self.cfg["in_games"])
        self._update_hint()
        self.parent().update_controller_hint()

    def _autostart(self):
        if self.vr:
            self.vr.set_autolaunch(self.cfg["autostart"])
        if not self.cfg["autostart"]:
            config_mod.remove_desktop_autostart()

    def _turn_on(self):
        if self.vr:
            self.vr.enable_global_input()
        self.turn_on.setText("Turning on...")

    def _refresh_test(self):
        snap = self.vr.snapshot() if self.vr else {}
        self.vr_line.setText(snap.get("summary", "SteamVR isn't connected."))
        self.turn_on.setVisible(snap.get("status") in ("setting", "partial"))
        lit = set(snap.get("held", ())) | {f"btn:{b}" for b in snap.get("buttons", ())}
        bound = snap.get("bound", {})
        usable = {f"btn:{b}" for b, on in bound.items() if on}
        if snap.get("read_only"):
            usable |= {f"btn:{b}" for b in BUTTONS}
        if usable:
            usable |= {"talk", "copy", "paste", "select_all"}
        for key, pill in self.pills.items():
            if key in lit:
                style = f"background: {ACCENT}; color: #04140f;"
            elif key in usable:
                style = f"background: {CARD_HOVER}; color: {TEXT};"
            else:
                style = f"background: transparent; color: #4b5263; border: 1px dashed {BORDER};"
            pill.setStyleSheet(style + " border-radius: 10px; font-weight: 600;")

    def _customize(self):
        if not (self.vr and self.vr.open_bindings()):
            self.preset_hint.setText(
                "Open SteamVR Settings > Controllers > Manage Controller Bindings, "
                "then pick fuelCell Voice Typing.")

    def _check(self):
        # A separate process: the check talks to SteamVR on its own, which
        # mustn't disturb this app's connection.
        if self._proc is not None:
            return
        self.output.setPlainText("Checking...")
        self.output.show()
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.finished.connect(self._check_done)
        self._proc.start(sys.executable, ["-m", "frame_voice", "--check"])

    def _check_done(self, *_):
        proc, self._proc = self._proc, None
        if proc is not None:
            text = bytes(proc.readAll()).decode(errors="replace")
            self.output.setPlainText(text or "The check didn't run.")

    def done(self, result):
        if self._proc is not None:  # closed while the check runs
            self._proc.finished.disconnect(self._check_done)
            self._proc.kill()
            self._proc.waitForFinished(1000)
            self._proc = None
        super().done(result)


# -- main window ----------------------------------------------------------------
class Bridge(QObject):
    state = Signal(str, str)
    transcript = Signal(str)
    vr_status = Signal(str)


KEYS = [
    ("copy", "Copy"), ("paste", "Paste"), ("cut", "Cut"), ("select_all", "Select all"),
    ("undo", "Undo"), ("backspace", "Delete"), ("space", "Space"), ("enter", "Enter"),
]


class MainWindow(QWidget):
    def __init__(self, cfg, engine_cls):
        # Never take keyboard focus: pressing our buttons must leave the cursor
        # in the text box the user was typing in, like an on-screen keyboard.
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("fuelCell Voice Typing")
        self.setWindowIcon(app_icon())
        self.cfg = cfg
        self._active = False  # read from worker threads
        self._testing = False  # Settings is open: controller buttons only light up there
        self._window_ids = ()
        self._shown = False
        self._plain_ready = False  # the hint shows the usual "how to talk" text
        self.bridge = Bridge()
        self.engine = engine_cls(cfg, on_state=self.bridge.state.emit,
                                 on_transcript=self.bridge.transcript.emit,
                                 before_input=self.give_focus_back)
        self.bridge.state.connect(self.on_state)
        self.bridge.transcript.connect(self.on_transcript)
        QApplication.instance().installEventFilter(self)
        self.vr = SteamVR(self.on_controller, cfg["controller_preset"],
                          on_status=self.bridge.vr_status.emit, edits=cfg["edit_shortcuts"],
                          in_games=cfg["in_games"], autolaunch=cfg["autostart"])
        self.bridge.vr_status.connect(self.on_vr_status)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 22)
        root.setSpacing(0)

        # header
        head = QHBoxLayout()
        mark = QLabel(f'<span style="color:{TEXT}">fuel</span>'
                      f'<span style="color:{ACCENT}">C</span>'
                      f'<span style="color:{TEXT}">ell</span>')
        mark.setFont(font(22, QFont.Bold, -0.4))
        sub = QLabel("Voice Typing")
        sub.setObjectName("muted")
        sub.setFont(font(15, QFont.DemiBold))
        gear = QToolButton()
        gear.setObjectName("gear")
        gear.setIcon(draw_icon("gear", 24, MUTED))
        gear.setIconSize(QSize(24, 24))
        gear.setFixedSize(40, 40)
        gear.setCursor(Qt.PointingHandCursor)
        gear.setFocusPolicy(Qt.NoFocus)
        gear.setToolTip("Settings")
        gear.clicked.connect(self.open_settings)
        head.addWidget(mark)
        head.addSpacing(8)
        head.addWidget(sub)
        head.addStretch()
        head.addWidget(gear)
        root.addLayout(head)
        root.addSpacing(6)

        # mic + status
        self.mic = MicButton()
        self.mic.clicked.connect(self.on_mic)
        root.addWidget(self.mic, 0, Qt.AlignHCenter)
        self.status = QLabel("Getting ready...")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)
        root.addSpacing(4)
        self.hint = QLabel(self.ready_hint())
        self.hint.setObjectName("hint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        root.addSpacing(18)

        # last dictation card
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 12, 12)
        cl.setSpacing(6)
        top = QHBoxLayout()
        t = QLabel("LAST TYPED")
        t.setObjectName("cardTitle")
        top.addWidget(t)
        top.addStretch()
        self.again = QPushButton("Type again")
        self.copy_text = QPushButton("Copy text")
        for b in (self.again, self.copy_text):
            b.setObjectName("small")
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(False)
            top.addWidget(b)
        self.again.clicked.connect(self.engine.retype_last)
        self.copy_text.clicked.connect(self.copy_last)
        cl.addLayout(top)
        self.last = QLabel("Nothing yet")
        self.last.setObjectName("lastText")
        self.last.setWordWrap(True)
        self.last.setStyleSheet(f"color: {MUTED};")
        cl.addWidget(self.last)
        root.addWidget(card)
        root.addSpacing(14)

        # edit keys
        grid = QGridLayout()
        grid.setSpacing(10)
        self.keys = []
        for i, (action, label) in enumerate(KEYS):
            b = QToolButton()
            b.setObjectName("key")
            b.setText(label)
            b.setIcon(draw_icon(action, 26))
            b.setIconSize(QSize(26, 26))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumSize(96, 84)
            b.clicked.connect(lambda _=False, a=action: self.run_shortcut(a))
            grid.addWidget(b, i // 4, i % 4)
            self.keys.append(b)
        root.addLayout(grid)
        root.addSpacing(14)

        # controller shortcut hint
        foot = QHBoxLayout()
        self.vr_dot = QLabel()
        self.vr_dot.setFixedSize(10, 10)
        self.controller = QLabel()
        self.controller.setObjectName("hint")
        self.controller.setWordWrap(True)
        foot.addWidget(self.vr_dot, 0, Qt.AlignTop)
        foot.addSpacing(4)
        foot.addWidget(self.controller, 1)
        self.turn_on = QPushButton("Turn on")
        self.turn_on.setObjectName("small")
        self.turn_on.setFocusPolicy(Qt.NoFocus)
        self.turn_on.setCursor(Qt.PointingHandCursor)
        self.turn_on.clicked.connect(self.turn_on_global_input)
        self.turn_on.hide()
        foot.addWidget(self.turn_on, 0, Qt.AlignVCenter)
        root.addLayout(foot)
        self.on_vr_status("")

        self.setFixedWidth(470)
        self.adjustSize()

    def start(self):
        threading.Thread(target=self._load, daemon=True).start()
        self.vr.start()

    def _load(self):
        self.engine.load()
        if self.engine.state == READY and self.cfg.get("keyboard_hotkeys"):
            self.engine.start_hotkeys()

    def on_vr_status(self, _text=""):
        status = self.vr.status
        color = {"on": ACCENT, "partial": ACCENT, "readonly": ACCENT, "setting": AMBER,
                 "nobind": AMBER, "error": RED}.get(status, MUTED)
        self.vr_dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self.turn_on.setVisible(status in ("setting", "partial"))
        self.turn_on.setText("Turn on")
        self.update_controller_hint()

    def update_controller_hint(self):
        if self.vr.status in ("on", "readonly"):
            self.controller.setText(preset_hint(self.cfg["controller_preset"],
                                                self.cfg["edit_shortcuts"]))
        elif self.vr.status == "setting":
            self.controller.setText("Controller shortcuts need one SteamVR setting.")
        elif self.vr.status == "partial":
            self.controller.setText("Shortcuts work. Turn on one SteamVR setting so the "
                                    "buttons don't also reach other apps.")
        else:
            self.controller.setText(self.vr.summary)
        if self._plain_ready:
            self.hint.setText(self.ready_hint())

    def ready_hint(self):
        if self.vr.status not in ("on", "partial", "readonly"):
            return "Click a text box first, then tap the mic."
        label = PRESETS[self.cfg["controller_preset"]][0]
        combo = label[5:] if label.startswith("Hold ") else label
        return f"Click a text box, then hold {combo} and talk."

    def turn_on_global_input(self):
        self.vr.enable_global_input()
        self.turn_on.setText("Turning on...")

    def on_controller(self, action, pressed):
        """Controller combos from the SteamVR thread."""
        if self._testing and pressed:
            return
        self.engine.hotkey(action, pressed)

    def on_mic(self):
        if self.engine.state == ERROR:
            self.start()  # retry
        else:
            self.engine.toggle_talking()

    def on_state(self, state, message):
        self.vr.show_state(state, message)
        self.mic.set_state(state)
        color = {ERROR: RED, LISTENING: RED, WORKING: AMBER}.get(state, TEXT)
        self.status.setStyleSheet(f"color: {color};")
        texts = {
            READY: ("Tap to talk", self.ready_hint()),
            LISTENING: ("Listening...", "Tap again when you're done."),
            WORKING: ("Typing...", "Turning your words into text."),
            LOADING: (message or "Getting ready...", "This only takes a moment."),
            ERROR: ("Something's wrong", message + "\nTap the mic to try again."),
        }
        title, hint = texts.get(state, (message, ""))
        self._plain_ready = state == READY and message in ("", "Ready")
        if state == READY and not self._plain_ready:
            hint = message
        self.status.setText(title)
        self.hint.setText(hint)
        ok = state not in (LOADING, ERROR)
        for b in self.keys:
            b.setEnabled(ok)
        self.again.setEnabled(ok and bool(self.engine.last_text))

    def on_transcript(self, text):
        QGuiApplication.clipboard().setText(text)
        self.last.setText(f"“{text}”")
        self.last.setStyleSheet(f"color: {TEXT};")
        self.again.setEnabled(True)
        self.copy_text.setEnabled(True)

    def copy_last(self):
        if self.engine.last_text:
            QGuiApplication.clipboard().setText(self.engine.last_text)
            self.copy_text.setText("Copied!")
            QTimer.singleShot(1200, lambda: self.copy_text.setText("Copy text"))

    # -- keeping keys out of our own window ---------------------------------
    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange:
            self._active = self.isActiveWindow()
        super().changeEvent(event)

    def showEvent(self, event):
        self._window_ids = (int(self.winId()),)
        super().showEvent(event)

    KEY_EVENTS = (QEvent.KeyPress, QEvent.KeyRelease, QEvent.ShortcutOverride)

    def eventFilter(self, obj, event):
        # Never react to typed keys in any of our windows: if one ends up with
        # focus, those keys were meant for another app (a space used to
        # "click" Settings). The app has no text fields, so nothing is lost.
        return event.type() in self.KEY_EVENTS and isinstance(obj, QWidget)

    def give_focus_back(self):
        """Called from a worker thread right before keys are sent."""
        if not self._active:
            return True
        log.info("our window is active; handing focus back before typing")
        if focus.activate_previous(self._window_ids):
            for _ in range(20):  # wait up to 1 s for the switch
                if not self._active:
                    time.sleep(0.05)  # let the other window settle
                    return True
                time.sleep(0.05)
        log.warning("couldn't hand focus back")
        return False

    def run_shortcut(self, action):
        threading.Thread(target=self.engine.shortcut, args=(action,), daemon=True).start()

    def closeEvent(self, event):
        # Keep running so controller shortcuts keep working; opening the app
        # again brings this window back. Settings has a Quit button.
        event.ignore()
        self.hide()

    def present(self, fade=True):
        """Show the window (centred the first time), fading in."""
        if not self._shown:
            self._shown = True
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())
        if self.isVisible():
            self.raise_()
            return
        self.setWindowOpacity(0.0 if fade else 1.0)
        self.show()
        self.raise_()
        if fade:
            anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=220,
                                     easingCurve=QEasingCurve.OutCubic)
            anim.valueChanged.connect(self.setWindowOpacity)
            anim.start()

    def bring_back(self):
        self.present()

    def open_settings(self):
        self._testing = True
        try:
            Settings(self, self.cfg, self.engine, self.vr).exec()
        finally:
            self._testing = False


def run(cfg, engine_cls, show_intro=True, background=False):
    # Under XWayland the "never take focus" hint is honoured reliably.
    if os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("frame-voice")
    app.setApplicationDisplayName("fuelCell Voice Typing")
    app.setDesktopFileName("frame-voice")
    app.setFont(font(15))
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())

    # Only one copy runs. Opening the app again just shows the window.
    name = f"frame-voice-{os.getuid()}"
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer(app)
    server.listen(name)

    win = MainWindow(cfg, engine_cls)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win.bring_back()))
    win.start()  # load the speech model while the intro plays
    # On KDE, make sure clicking our window never moves keyboard focus.
    threading.Thread(target=focus.install_kwin_rule, daemon=True).start()

    if background:
        pass  # started by SteamVR: shortcuts only until the app is opened
    elif show_intro:
        intro = Intro()
        intro.finished.connect(win.present)
        intro.start()
    else:
        win.present()
    return app.exec()
