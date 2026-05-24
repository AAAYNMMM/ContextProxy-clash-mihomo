from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPushButton


class ProxySwitch(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(62, 34)
        self.setText("")
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_to_state)

    def sizeHint(self):
        return QSize(62, 34)

    def set_running(self, running: bool):
        self.blockSignals(True)
        self.setChecked(running)
        self._offset = 1.0 if running else 0.0
        self.blockSignals(False)
        self.update()

    def _animate_to_state(self, checked: bool):
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _get_offset(self):
        return self._offset

    def _set_offset(self, value):
        self._offset = float(value)
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        track_color = QColor("#16A34A" if self.isChecked() else "#CBD5E1")
        if not self.isEnabled():
            track_color = QColor("#E5E7EB")

        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, 16, 16)

        knob_size = 28
        x = 3 + self._offset * (self.width() - knob_size - 6)
        knob_rect = QRectF(x, 3, knob_size, knob_size)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(knob_rect)
