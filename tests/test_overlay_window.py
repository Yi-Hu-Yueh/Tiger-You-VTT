from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QApplication

from app.overlay.caption_state import CaptionState
from app.overlay.overlay_window import CaptionOverlayWindow
from app.overlay.settings import OverlaySettings


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def window(settings: OverlaySettings | None = None) -> CaptionOverlayWindow:
    app()
    return CaptionOverlayWindow(settings)


def test_overlay_starts_with_waiting_caption() -> None:
    widget = window()
    assert widget.caption_label.text() == "等待字幕..."
    assert widget.settings.position == "bottom"
    assert widget.settings.font_size == 32
    assert widget.settings.background_opacity == 70
    assert widget.settings.display_lines == 2
    assert widget.settings.always_on_top is True
    widget.close()


def test_overlay_is_frameless() -> None:
    widget = window()
    assert widget.windowFlags() & Qt.WindowType.FramelessWindowHint
    widget.close()


def test_overlay_is_topmost_by_default() -> None:
    widget = window()
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    widget.close()


def test_topmost_can_be_disabled() -> None:
    widget = window(OverlaySettings(always_on_top=False))
    assert not widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    widget.close()


def test_font_size_is_applied() -> None:
    widget = window(OverlaySettings(font_size=48))
    assert widget.caption_label.font().pixelSize() == 48
    widget.close()


def test_background_opacity_is_applied_without_window_opacity() -> None:
    widget = window(OverlaySettings(background_opacity=40))
    assert "rgba(0, 0, 0, 102)" in widget.caption_label.styleSheet()
    assert widget.windowOpacity() == 1.0
    widget.close()


def test_top_position_is_centered_near_top() -> None:
    point = CaptionOverlayWindow.calculate_position(
        QRect(100, 200, 1200, 700), QSize(600, 100), "top"
    )
    assert point.x() == 400
    assert point.y() == 240


def test_bottom_position_is_centered_near_bottom() -> None:
    point = CaptionOverlayWindow.calculate_position(
        QRect(100, 200, 1200, 700), QSize(600, 100), "bottom"
    )
    assert point.x() == 400
    assert point.y() == 760


def test_caption_text_wraps_and_updates() -> None:
    widget = window()
    state = CaptionState(1)
    state.update(
        [
            {"start": 0.0, "end": 1.0, "text": "較早的字幕"},
            {"start": 1.0, "end": 2.0, "text": "新的即時字幕"},
        ]
    )
    widget.set_caption(state.rendered_text())
    assert widget.caption_label.wordWrap() is True
    assert widget.caption_label.text() == "新的即時字幕"
    widget.close()


def test_overlay_can_be_shown_and_hidden() -> None:
    widget = window()
    widget.show()
    app().processEvents()
    assert widget.isVisible()
    widget.hide()
    assert not widget.isVisible()
    widget.close()
