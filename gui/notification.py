from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QDialog, QGraphicsOpacityEffect, QLabel, QVBoxLayout


SUCCESS = "#16A34A"
ERROR = "#EF4444"
WARNING = "#F59E0B"
CARD = "#FFFFFF"
SHADOW = "rgba(15, 23, 42, 0.16)"


class IconAnimation(QDialog):
    def __init__(self, parent, symbol: str, color: str, duration: int = 1000):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(False)

        icon = QLabel(symbol)
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(76, 76)
        icon.setStyleSheet(
            f"""
            QLabel {{
                background: {color};
                color: white;
                border-radius: 38px;
                font-size: 42px;
                font-weight: 900;
                border: 6px solid {CARD};
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(icon)

        self._duration = duration
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)

        self._fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_in.setDuration(120)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)

        self._fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_out.setDuration(180)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self.close)
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self._move_to_parent_center()
        self._fade_in.start()
        QTimer.singleShot(self._duration, self._fade_out.start)

    def _move_to_parent_center(self):
        parent = self.parentWidget()
        if not parent:
            return

        parent_rect = parent.frameGeometry()
        x = parent_rect.x() + (parent_rect.width() - self.width()) // 2
        y = parent_rect.y() + (parent_rect.height() - self.height()) // 2
        self.move(x, y)


def show_success_animation(parent, duration=1000):
    dialog = IconAnimation(parent, "✓", SUCCESS, duration)
    dialog.show()
    return dialog


def show_error_animation(parent, duration=1200):
    dialog = IconAnimation(parent, "×", ERROR, duration)
    dialog.show()
    return dialog


def show_warning_animation(parent, duration=1000):
    dialog = IconAnimation(parent, "!", WARNING, duration)
    dialog.show()
    return dialog


def show_success_toast(parent, _message="", duration=1000):
    return show_success_animation(parent, duration)


def show_info_toast(parent, _message="", duration=1000):
    return show_success_animation(parent, duration)


def show_error_dialog(parent, _message=""):
    return show_error_animation(parent)
