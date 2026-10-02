from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from app.config import WHISPER_SETTINGS, WhisperSettings
from app.services.transcript import normalize_transcribed_segments
from app.services.youtube import VideoExtractionError


@dataclass(frozen=True)
class TranscriptionResult:
    segments: list[dict[str, Any]]
    language: str
    model: str
    device: str
    compute_type: str
    duration_seconds: float


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except (ImportError, OSError, RuntimeError, ValueError):
        return 0


def supported_compute_types(device: str) -> set[str]:
    try:
        import ctranslate2

        return set(ctranslate2.get_supported_compute_types(device))
    except (ImportError, OSError, RuntimeError, ValueError):
        return set()


def _default_model_factory(model: str, device: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(model, device=device, compute_type=compute_type)


class WhisperTranscriber:
    def __init__(
        self,
        settings: WhisperSettings = WHISPER_SETTINGS,
        model_factory: Callable[[str, str, str], Any] = _default_model_factory,
        cuda_counter: Callable[[], int] = cuda_device_count,
        compute_type_provider: Callable[[str], set[str]] = supported_compute_types,
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory
        self._cuda_counter = cuda_counter
        self._compute_type_provider = compute_type_provider
        self._lock = Lock()
        self._model: Any | None = None
        self._device: str | None = None
        self._compute_type: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _compute_for(self, device: str) -> str:
        if self.settings.compute_type != "auto":
            return self.settings.compute_type
        supported = self._compute_type_provider(device)
        preferences = (
            ("int8_float16", "int8_float32", "int8", "float32")
            if device == "cuda"
            else ("int8", "int8_float32", "float32")
        )
        for compute_type in preferences:
            if compute_type in supported:
                return compute_type
        raise RuntimeError(
            f"CTranslate2 reported no supported compute type for {device}"
        )

    def _candidates(self) -> list[tuple[str, str]]:
        if self.settings.device == "auto":
            if self._cuda_counter() > 0:
                try:
                    cuda_compute = self._compute_for("cuda")
                except RuntimeError:
                    pass
                else:
                    return [
                        ("cuda", cuda_compute),
                        ("cpu", self._compute_for("cpu")),
                    ]
            return [("cpu", self._compute_for("cpu"))]
        return [
            (self.settings.device, self._compute_for(self.settings.device))
        ]

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model

            try:
                candidates = self._candidates()
            except Exception as exc:
                raise VideoExtractionError(
                    503,
                    "whisper_model_load_failed",
                    "The local faster-whisper model could not be loaded.",
                ) from exc

            last_error: Exception | None = None
            for device, compute_type in candidates:
                try:
                    model = self._model_factory(
                        self.settings.model, device, compute_type
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                self._model = model
                self._device = device
                self._compute_type = compute_type
                return model

            raise VideoExtractionError(
                503,
                "whisper_model_load_failed",
                "The local faster-whisper model could not be loaded.",
            ) from last_error

    def _activate_cpu_fallback(self) -> Any:
        with self._lock:
            if self._model is not None and self._device == "cpu":
                return self._model
            try:
                compute_type = self._compute_for("cpu")
                model = self._model_factory(
                    self.settings.model, "cpu", compute_type
                )
            except Exception as exc:
                raise VideoExtractionError(
                    503,
                    "whisper_model_load_failed",
                    "The local faster-whisper model could not be loaded.",
                ) from exc
            self._model = model
            self._device = "cpu"
            self._compute_type = compute_type
            return model

    @staticmethod
    def _decode(model: Any, audio_path: Path) -> tuple[list[dict[str, Any]], Any]:
        generated_segments, info = model.transcribe(
            str(audio_path), beam_size=5, vad_filter=True
        )
        raw_segments = [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            }
            for segment in generated_segments
        ]
        return normalize_transcribed_segments(raw_segments), info

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        model = self._get_model()
        started = perf_counter()
        try:
            segments, info = self._decode(model, audio_path)
        except VideoExtractionError:
            raise
        except Exception as exc:
            if self.settings.device == "auto" and self._device == "cuda":
                model = self._activate_cpu_fallback()
                try:
                    segments, info = self._decode(model, audio_path)
                except Exception as fallback_exc:
                    raise VideoExtractionError(
                        502,
                        "whisper_transcription_failed",
                        "Local faster-whisper transcription failed.",
                    ) from fallback_exc
            else:
                raise VideoExtractionError(
                    502,
                    "whisper_transcription_failed",
                    "Local faster-whisper transcription failed.",
                ) from exc

        duration = round(perf_counter() - started, 3)
        if not segments:
            raise VideoExtractionError(
                502,
                "whisper_empty_transcript",
                "Local faster-whisper produced no usable transcript segments.",
            )

        language = getattr(info, "language", None)
        if not isinstance(language, str) or not language.strip():
            raise VideoExtractionError(
                502,
                "whisper_transcription_failed",
                "Local faster-whisper did not report a detected language.",
            )

        return TranscriptionResult(
            segments=segments,
            language=language.strip(),
            model=self.settings.model,
            device=self._device or "cpu",
            compute_type=self._compute_type or self._compute_for("cpu"),
            duration_seconds=duration,
        )


TRANSCRIBER = WhisperTranscriber()


def transcribe_audio(audio_path: Path) -> TranscriptionResult:
    return TRANSCRIBER.transcribe(audio_path)
