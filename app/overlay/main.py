"""Entry point for the optional native Windows caption overlay."""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is required for the native overlay. "
            "Install it with: pip install -r requirements-overlay.txt",
            file=sys.stderr,
        )
        return 1

    from app.overlay.api_client import OverlayApiClient
    from app.overlay.control_window import OverlayControlWindow
    from app.overlay.overlay_window import CaptionOverlayWindow
    from app.overlay.settings import OverlaySettings

    application = QApplication(sys.argv)
    application.setApplicationName("Tiger-You-VTT Caption Overlay")
    application.setOrganizationName("Tiger-You-VTT")
    store = QSettings()
    settings = OverlaySettings.from_store(store)
    overlay = CaptionOverlayWindow(settings)
    client = OverlayApiClient(
        os.environ.get("TIGER_YOU_VTT_BACKEND_URL", "http://127.0.0.1:8000")
    )
    controls = OverlayControlWindow(overlay, client, store)
    controls.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
