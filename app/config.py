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


@dataclass(frozen=True)
class SystemAudioSettings:
    chunk_seconds: float
    frames_per_buffer: int
    silence_peak_threshold: int = 16


@dataclass(frozen=True)
class MicrophoneSettings:
    chunk_seconds: float
    frames_per_buffer: int
    silence_peak_threshold: int = 16


@dataclass(frozen=True)
class DiarizationSettings:
    model: str
    device: str


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


def _positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
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

SYSTEM_AUDIO_SETTINGS = SystemAudioSettings(
    chunk_seconds=_positive_float("SYSTEM_AUDIO_CHUNK_SECONDS", 10.0),
    frames_per_buffer=_positive_int("SYSTEM_AUDIO_FRAMES_PER_BUFFER", 1024),
    silence_peak_threshold=_positive_int(
        "SYSTEM_AUDIO_SILENCE_PEAK_THRESHOLD", 16
    ),
)

MICROPHONE_SETTINGS = MicrophoneSettings(
    chunk_seconds=_positive_float("MICROPHONE_CHUNK_SECONDS", 10.0),
    frames_per_buffer=_positive_int("MICROPHONE_FRAMES_PER_BUFFER", 1024),
    silence_peak_threshold=_positive_int(
        "MICROPHONE_SILENCE_PEAK_THRESHOLD", 16
    ),
)

DIARIZATION_SETTINGS = DiarizationSettings(
    model=_choice(
        "DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1"
    ),
    device=_choice("DIARIZATION_DEVICE", "cpu", {"cpu", "cuda"}),
)

YOUTUBE_SEARCH_DEFAULT_LIMIT = 10
YOUTUBE_SEARCH_MAX_LIMIT = 20
YOUTUBE_SEARCH_CANDIDATE_LIMIT = 30
