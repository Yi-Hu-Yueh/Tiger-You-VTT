from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WhisperSettings:
    model: str
    device: str
    compute_type: str


@dataclass(frozen=True)
class VideoUploadSettings:
    max_bytes: int
    chunk_bytes: int


def _choice(name: str, default: str, allowed: set[str] | None = None) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise RuntimeError(f"{name} cannot be empty")
    if allowed is not None and value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RuntimeError(f"{name} must be one of: {choices}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


WHISPER_SETTINGS = WhisperSettings(
    model=_choice("WHISPER_MODEL", "large-v3"),
    device=_choice("WHISPER_DEVICE", "auto", {"auto", "cpu", "cuda"}),
    compute_type=_choice("WHISPER_COMPUTE_TYPE", "auto"),
)

VIDEO_UPLOAD_SETTINGS = VideoUploadSettings(
    max_bytes=_positive_int("VIDEO_UPLOAD_MAX_BYTES", 10 * 1024**3),
    chunk_bytes=_positive_int("VIDEO_UPLOAD_CHUNK_BYTES", 1024**2),
)
