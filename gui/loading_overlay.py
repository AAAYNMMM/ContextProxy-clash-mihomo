from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget


class LoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.hide()

        self._angle = 0
        self._message = ""
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet(
            """
            QLabel {
                color: #334155;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }
            """
        )

    def start(self, message: str = "正在处理..."):
        self._message = message or ""
        self._label.setText(self._message)
        if self.parentWidget():
            self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.setFocus(Qt.OtherFocusReason)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_label()

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def keyPressEvent(self, event):
        event.accept()

    def keyReleaseEvent(self, event):
        event.accept()

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(245, 247, 251, 168))

        center = self.rect().center()
        card_width = 138
        card_height = 112 if self._message else 92
        card_rect = self.rect()
        card_rect.setWidth(card_width)
        card_rect.setHeight(card_height)
        card_rect.moveCenter(center)

        painter.setBrush(QColor(255, 255, 255, 236))
        painter.setPen(QPen(QColor(226, 232, 240), 1))
        painter.drawRoundedRect(card_rect, 14, 14)

        spinner_center = card_rect.center()
        spinner_center.setY(card_rect.top() + 42)
        radius = 22
        for index in range(12):
            alpha = 44 + index * 17
            painter.save()
            painter.translate(spinner_center)
            painter.rotate(self._angle + index * 30)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(37, 99, 235, min(alpha, 255)))
            painter.drawRoundedRect(radius - 4, -3, 11, 6, 3, 3)
            painter.restore()

        self._position_label(card_rect)

    def _tick(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def _position_label(self, card_rect=None):
        if not self._message:
            self._label.hide()
            return

        self._label.show()
        rect = card_rect or self.rect()
        if card_rect is None:
            card_width = 138
            card_height = 112
            card_rect = self.rect()
            card_rect.setWidth(card_width)
            card_rect.setHeight(card_height)
            card_rect.moveCenter(self.rect().center())

        self._label.setGeometry(card_rect.left() + 8, card_rect.bottom() - 40, card_rect.width() - 16, 24)
