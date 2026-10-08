"""PC-local Deno discovery; never alters PATH or accesses credentials."""
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess

from yt_dlp.utils import DownloadError, ExtractorError

from app.services.errors import VideoExtractionError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TIGER_DENO_PATH = Path("D:/TigerTools/deno/deno.exe")
RUNTIME_ERROR_CODES = frozenset({"youtube_deno_configuration_invalid", "youtube_js_runtime_required"})


def configuration_error() -> VideoExtractionError:
    return VideoExtractionError(503, "youtube_deno_configuration_invalid",
        "PC 的 YOUTUBE_DENO_PATH 設定無效，請指定可執行的 Deno 2.3.0 以上版本檔案。")


def challenge_error() -> VideoExtractionError:
    return VideoExtractionError(503, "youtube_js_runtime_required",
        "YouTube JavaScript 驗證失敗，請在 PC 確認 Deno 與 yt-dlp-ejs，或設定 YOUTUBE_DENO_PATH 後重試。")


@lru_cache(maxsize=16)
def _supported_version(path: str, mtime_ns: int, size: int) -> bool:
    # Stat-based cache also rechecks a replaced executable. Capture, never log,
    # output from an explicitly configured executable; bound startup failures.
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        match = re.search(r"^deno (\d+)\.(\d+)\.(\d+)\b", result.stdout)
        return result.returncode == 0 and match is not None and tuple(map(int, match.groups())) >= (2, 3, 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _candidate(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    try:
        path = Path(str(value).strip()).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
        if not path.is_file():
            return None
        stat = path.stat()
        return path if _supported_version(str(path), stat.st_mtime_ns, stat.st_size) else None
    except (OSError, ValueError, RuntimeError):
        return None


def discover_deno() -> Path | None:
    override = os.environ.get("YOUTUBE_DENO_PATH")
    if override is not None:
        path = _candidate(override)
        if path is None:
            raise configuration_error() from None
        return path
    return _candidate(shutil.which("deno")) or _candidate(TIGER_DENO_PATH)


def javascript_options() -> dict:
    path = discover_deno()
    # An empty dict explicitly disables yt-dlp's implicit runtime discovery.
    return {"js_runtimes": {"deno": {"path": str(path)}} if path else {}}


def is_js_failure(message: str) -> bool:
    text = message.casefold()
    return any(marker in text for marker in (
        "n challenge solving failed", "signature solving failed",
        "javascript challenge solving failed", "no supported javascript runtime",
        "javascript runtime is required", "javascript runtime required",
    ))


def runtime_failure(error: Exception, challenge_failed: bool) -> bool:
    if not isinstance(error, (DownloadError, ExtractorError)):
        return False
    text = str(error).casefold()
    # A generic format failure alone is not evidence of a JS problem.
    return is_js_failure(text) or (challenge_failed and any(marker in text for marker in (
        "requested format is not available", "no video formats found", "no formats found",
    )))
