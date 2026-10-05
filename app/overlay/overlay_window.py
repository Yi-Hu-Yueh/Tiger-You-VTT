"""Native caption overlay window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.overlay.settings import OverlaySettings


class CaptionOverlayWindow(QWidget):
    """Frameless, topmost window used to display live captions."""

    def __init__(self, settings: OverlaySettings | None = None) -> None:
        super().__init__()
        self.settings = (settings or OverlaySettings()).validated()
        self._drag_offset: QPoint | None = None

        self.setObjectName("captionOverlayWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.caption_label = QLabel("等待字幕...")
        self.caption_label.setObjectName("captionLabel")
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption_label.setWordWrap(True)
        screen = self.screen()
        screen_width = screen.availableGeometry().width() if screen is not None else 1280
        caption_width = min(1400, max(720, round(screen_width * 0.75)))
        self.caption_label.setFixedWidth(caption_width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.addWidget(self.caption_label)

        self.apply_settings(self.settings)

    @staticmethod
    def calculate_position(
        available: QRect,
        size: QSize,
        position: str,
        margin: int = 40,
    ) -> QPoint:
        x = available.left() + max(0, (available.width() - size.width()) // 2)
        if position == "top":
            y = available.top() + margin
        else:
            y = available.bottom() - size.height() - margin + 1
        return QPoint(x, max(available.top(), y))

    def apply_settings(self, settings: OverlaySettings) -> None:
        self.settings = settings.validated()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)

        font = self.caption_label.font()
        font.setPixelSize(self.settings.font_size)
        font.setBold(True)
        self.caption_label.setFont(font)

        alpha = round(255 * self.settings.background_opacity / 100)
        self.caption_label.setStyleSheet(
            "QLabel {"
            f"background-color: rgba(0, 0, 0, {alpha});"
            "color: white; border-radius: 10px; padding: 12px 18px;"
            "}"
        )
        self.adjustSize()
        if self.settings.position != "custom":
            self.reposition()
        if was_visible:
            self.show()

    def reposition(self) -> None:
        if self.settings.position == "custom":
            return
        screen = self.screen()
        if screen is None:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(
                self.calculate_position(
                    screen.availableGeometry(), self.size(), self.settings.position
                )
            )

    def set_caption(self, text: str) -> None:
        self.caption_label.setText(text or "等待字幕...")
        self.adjustSize()
        if self.settings.position != "custom":
            self.reposition()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self.settings.position == "custom"
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self.settings.position == "custom"
            and self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)
