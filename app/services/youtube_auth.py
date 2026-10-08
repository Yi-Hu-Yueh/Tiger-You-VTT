"""Anonymous-first YouTube operations with one optional, PC-local cookie retry."""
from collections.abc import Callable, Mapping
from contextlib import contextmanager, redirect_stderr, suppress
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, TypeVar

from yt_dlp.cookies import CookieLoadError
from yt_dlp.utils import DownloadError, ExtractorError

from app.services.errors import VideoExtractionError
from app.services.youtube_runtime import (
    RUNTIME_ERROR_CODES, challenge_error, is_js_failure, javascript_options, runtime_failure,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTH_MESSAGE = "YouTube 要求登入驗證，請在 PC 設定本機 YouTube Cookie 後再試。"
_T = TypeVar("_T")
_COOKIE_LOAD_LOCK = RLock()


class QuietExtractorLogger:
    """Never echo upstream diagnostics that may contain cookie/header values."""
    def __init__(self):
        self.challenge_failed = False

    def debug(self, *_args, **_kwargs):
        pass

    info = error = debug

    def warning(self, message, *_args, **_kwargs):
        # Retain only a boolean, not potentially sensitive upstream text.
        if is_js_failure(str(message)) and "no supported javascript runtime" not in str(message).casefold():
            self.challenge_failed = True



def resolve_cookie_file() -> Path | None:
    configured = os.environ.get("YOUTUBE_COOKIES_FILE")
    try:
        if configured is not None:
            if not configured.strip():
                return None
            path = Path(configured.strip())
            if not path.is_absolute():
                path = PROJECT_ROOT / path
        else:
            path = PROJECT_ROOT / ".runtime" / "youtube_cookies.txt"
        path = path.resolve()
        if not path.is_file():
            return None
        # Test readability without reading/parsing session values for validation.
        with path.open("rb"):
            pass
        return path
    except (OSError, ValueError, RuntimeError):
        return None


def is_auth_challenge(error: BaseException) -> bool:
    if not isinstance(error, (DownloadError, ExtractorError)):
        return False
    text = str(error).casefold().replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s+", " ", text)
    # A login/cookies hint may accompany private/deleted/region failures.
    # The hint alone must never turn those into authentication retries.
    if any(marker in text for marker in (
        "private video", "video is private", "video has been removed", "video has been deleted",
        "video unavailable", "video is unavailable", "not available in your country",
        "not available from your location", "geo restricted", "unsupported url", "invalid url",
    )):
        # YouTube sometimes prefixes a real bot challenge with "Video unavailable".
        return bool(re.search(r"sign in to confirm you'?re not a bot", text))
    return any(re.search(pattern, text) for pattern in (
        r"sign in to confirm you'?re not a bot",
        r"sign in to confirm your age",
        r"\blogin[ _-]required\b",
        r"\bauthentication[ _-]required\b",
        r"this video is only available for registered users",
        r"you (?:must|need to) (?:log|sign) in (?:to|before)",
        r"sign in to (?:watch|view|access) (?:this|the) video",
    ))


def auth_required() -> VideoExtractionError:
    return VideoExtractionError(403, "youtube_auth_required", AUTH_MESSAGE)


def with_auth_fallback(operation: Callable[[Path | None], _T]) -> _T:
    try:
        return operation(None)
    except Exception as error:
        if not is_auth_challenge(error):
            if runtime_failure(error, False):
                raise challenge_error() from None
            raise
    cookie_file = resolve_cookie_file()
    if cookie_file is None:
        raise auth_required() from None
    try:
        return operation(cookie_file)
    except Exception as error:
        if isinstance(error, VideoExtractionError) and error.code in RUNTIME_ERROR_CODES:
            raise error from None
        if is_auth_challenge(error) or isinstance(error, CookieLoadError):
            raise auth_required() from None
        if runtime_failure(error, False):
            raise challenge_error() from None
        # Do not let an authenticated exception chain leak headers/cookie rows
        # through callers' logger.exception(), API responses, or background jobs.
        raise DownloadError("Authenticated YouTube operation failed.") from None


@contextmanager
def extractor(factory: Callable, options: Mapping[str, Any], cookie_file: Path | None = None):
    logger = QuietExtractorLogger()
    safe_options = {**options, **javascript_options(), "logger": logger}
    safe_options.pop("cookiefile", None)
    safe_options.pop("cookiesfrombrowser", None)
    if cookie_file is not None:
        safe_options["cookiefile"] = str(cookie_file)
        # Installed yt-dlp's cookie loader writes malformed cookie rows directly
        # to stderr, bypassing its logger. Suppress ONLY local loading, never the
        # network operation. Serialize these brief process-wide stderr guards.
        with _COOKIE_LOAD_LOCK, open(os.devnull, "w") as sink, redirect_stderr(sink):
            instance = factory(safe_options)
            try:
                _ = instance.cookiejar
            except Exception:
                # close() normally saves cookies and would retry a failed load.
                # Disable saving before cleanup; preserve the original file.
                with suppress(Exception):
                    instance.params["cookiefile"] = None
                    instance.close()
                raise CookieLoadError("Local YouTube cookie file could not be loaded.") from None
    else:
        instance = factory(safe_options)
    try:
        with instance as ydl:
            yield ydl
        if not safe_options["js_runtimes"] and logger.challenge_failed:
            raise challenge_error()
    except Exception as error:
        # Auth remains classified exclusively by the established cookie policy.
        if not is_auth_challenge(error) and runtime_failure(error, logger.challenge_failed):
            raise challenge_error() from None
        raise
