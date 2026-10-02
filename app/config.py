from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WhisperSettings:
    model: str
    device: str
    compute_type: str


def _choice(name: str, default: str, allowed: set[str] | None = None) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise RuntimeError(f"{name} cannot be empty")
    if allowed is not None and value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RuntimeError(f"{name} must be one of: {choices}")
    return value


WHISPER_SETTINGS = WhisperSettings(
    model=_choice("WHISPER_MODEL", "large-v3"),
    device=_choice("WHISPER_DEVICE", "auto", {"auto", "cpu", "cuda"}),
    compute_type=_choice("WHISPER_COMPUTE_TYPE", "auto"),
)
