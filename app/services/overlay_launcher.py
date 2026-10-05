"""Safe process ownership for the optional native caption overlay."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from threading import Lock
from typing import Any, Callable


class OverlayLaunchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OverlayLauncher:
    """Launch and track only the overlay process created by this instance."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        project_root: Path | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        spec_finder: Callable[[str], Any] = importlib.util.find_spec,
    ) -> None:
        self.executable = executable or sys.executable
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self._process_factory = process_factory
        self._spec_finder = spec_finder
        self._process: Any | None = None
        self._lock = Lock()

    def available(self) -> bool:
        try:
            return (
                self._spec_finder("PySide6") is not None
                and self._spec_finder("app.overlay.main") is not None
            )
        except (ImportError, ValueError):
            return False

    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def status(self) -> dict[str, bool]:
        return {"available": self.available(), "running": self.running()}

    def start(self) -> dict[str, bool | str]:
        if not self.available():
            raise OverlayLaunchError(
                "overlay_unavailable",
                "桌面字幕 Overlay 無法使用；請安裝選用的 Overlay 套件。",
            )

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return {
                    "available": True,
                    "running": True,
                    "status": "already_running",
                }

            creation_flags = 0
            if os.name == "nt":
                creation_flags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                )
            try:
                self._process = self._process_factory(
                    [self.executable, "-m", "app.overlay.main"],
                    cwd=str(self.project_root),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=creation_flags,
                )
            except OSError as exc:
                self._process = None
                raise OverlayLaunchError(
                    "overlay_launch_failed",
                    "桌面字幕 Overlay 啟動失敗。",
                ) from exc

            return {
                "available": True,
                "running": True,
                "status": "started",
            }
