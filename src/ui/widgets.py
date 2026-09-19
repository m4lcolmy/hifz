"""The two controls in the bar, painted rather than typed.

A QPushButton showing "▶" is a glyph in a font, and a font decides where
that glyph sits: ▶ carries its own side bearings and a baseline, so a circle
drawn around the *button* is never centred on the *triangle*. No amount of
padding fixes it, because the offset changes with whichever font the system
actually resolved. The stylesheet here had a `padding-top: 1px` fighting
exactly that, and lost.

Painting the shape removes the font from the question: the triangle is where
the geometry says it is, on every machine.
"""

from PyQt6.QtWidgets import QPushButton, QLabel, QWidget
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics
from PyQt6.QtCore import Qt, QRectF, QSize

from src.ui.style import (
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, BORDER,
    ACCENT, ACCENT_HOVER, ACCENT_PRESSED, BG_PRIMARY,
)


class _PaintedButton(QPushButton):
    """Shared hover tracking. Qt gives :hover to the stylesheet, not to us."""

    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self._hovered = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        return self.size()


class TransportButton(_PaintedButton):
    """Play / stop. The one control the reciter uses mid-session."""

    def __init__(self, size: int = 46, parent=None):
        super().__init__(size, parent)
        self.setCheckable(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Inset by half the ring width, so the stroke lands inside the widget
        # instead of being clipped by it.
        ring_width = 1.6
        bounds = QRectF(self.rect()).adjusted(
            ring_width, ring_width, -ring_width, -ring_width)
        listening = self.isChecked()

        if not self.isEnabled():
            ring, fill, ink = QColor(BORDER), QColor(0, 0, 0, 0), QColor(TEXT_MUTED)
        elif listening:
            base = QColor(ACCENT_PRESSED if self.isDown()
                          else ACCENT_HOVER if self._hovered else ACCENT)
            ring, fill, ink = base, base, QColor(BG_PRIMARY)
        else:
            ring = ink = QColor(TEXT_PRIMARY)
            fill = QColor(0, 0, 0, 20 if self.isDown() else 10 if self._hovered else 0)

        painter.setPen(QPen(ring, ring_width))
        painter.setBrush(fill)
        painter.drawEllipse(bounds)

        # Stroked as well as filled, with a round join: the corners of a
        # small triangle read as chipped otherwise.
        painter.setPen(QPen(ink, 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(ink)
        painter.drawPath(self._stop_path(bounds) if listening
                         else self._play_path(bounds))
        painter.end()

    @staticmethod
    def _play_path(bounds: QRectF) -> QPainterPath:
        centre = bounds.center()
        height = bounds.height() * 0.31
        width = height * 0.88
        # A triangle's mass sits behind its point, so a bounding box centred
        # on the circle reads as sitting left of it. Nudging the box right by
        # a twelfth of its width is the standard correction, and it is why
        # this is not simply centre.x() - width / 2.
        left = centre.x() - width / 2.0 + width / 12.0
        path = QPainterPath()
        path.moveTo(left, centre.y() - height / 2.0)
        path.lineTo(left + width, centre.y())
        path.lineTo(left, centre.y() + height / 2.0)
        path.closeSubpath()
        return path

    @staticmethod
    def _stop_path(bounds: QRectF) -> QPainterPath:
        centre = bounds.center()
        side = bounds.height() * 0.28
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(centre.x() - side / 2.0, centre.y() - side / 2.0, side, side),
            2.0, 2.0)
        return path


class SettingsButton(_PaintedButton):
    """Two sliders. What the popup holds is levels and choices, not a machine."""

    def __init__(self, size: int = 34, parent=None):
        super().__init__(size, parent)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._hovered or self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 20 if self.isDown() else 10))
            painter.drawRoundedRect(QRectF(self.rect()), 9.0, 9.0)

        ink = QColor(TEXT_PRIMARY if self._hovered else TEXT_SECONDARY)
        centre = QRectF(self.rect()).center()
        reach = self.width() * 0.24
        offset = self.height() * 0.15

        painter.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for row, knob_at in ((-offset, -0.25), (offset, 0.30)):
            y = centre.y() + row
            painter.drawLine(int(centre.x() - reach), int(y),
                             int(centre.x() + reach), int(y))
            painter.setBrush(ink)
            knob = reach * 2 * knob_at
            painter.drawEllipse(QRectF(centre.x() + knob - 2.6, y - 2.6, 5.2, 5.2))
        painter.end()


class StatusDot(QWidget):
    """A filled circle in the bar's state colour.

    The state used to be carried by the colour of the text itself, which
    meant reading the text to notice it had changed. A dot is the part you
    see without reading.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._color = QColor(TEXT_MUTED)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class StatusLabel(QLabel):
    """Right-hand state text, elided to whatever room it has.

    It used to size itself to its content, and the content is sometimes a
    filesystem path — so finishing a recorded session grew the label and
    shoved the play button off the centre of the window. The full text is
    kept as the tooltip, because a truncated path is no use to anyone
    looking for the file.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setAlignment(Qt.AlignmentFlag.AlignRight
                          | Qt.AlignmentFlag.AlignVCenter)

    def set_message(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width() - 2)))
