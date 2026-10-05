from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
import os
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from app.config import WHISPER_SETTINGS, WhisperSettings
from app.services.errors import VideoExtractionError
from app.services.transcript import normalize_transcribed_segments


_CUDA_RUNTIME_LOCK = Lock()
_CUDA_RUNTIME_DIRECTORY: Path | None = None
_CUDA_DLL_DIRECTORY_HANDLES: list[Any] = []


def _cuda_runtime_candidates() -> list[Path]:
    configured = os.getenv("WHISPER_CUDA_RUNTIME_DIR", "").strip()
    candidates = [Path(configured)] if configured else []

    try:
        torch_spec = find_spec("torch")
    except (ImportError, ModuleNotFoundError, ValueError):
        torch_spec = None
    if torch_spec is not None and torch_spec.submodule_search_locations:
        for package_directory in torch_spec.submodule_search_locations:
            candidates.append(Path(package_directory) / "lib")

    return candidates


def _configure_windows_cuda_runtime() -> Path | None:
    """Expose venv-local CUDA DLLs to CTranslate2 without changing Windows."""
    global _CUDA_RUNTIME_DIRECTORY

    if os.name != "nt":
        return None
    if _CUDA_RUNTIME_DIRECTORY is not None:
        return _CUDA_RUNTIME_DIRECTORY

    with _CUDA_RUNTIME_LOCK:
        if _CUDA_RUNTIME_DIRECTORY is not None:
            return _CUDA_RUNTIME_DIRECTORY

        for candidate in _cuda_runtime_candidates():
            directory = candidate.expanduser().resolve()
            required_dlls = (
                directory / "cublas64_12.dll",
                directory / "cublasLt64_12.dll",
            )
            if not all(path.is_file() for path in required_dlls):
                continue

            existing_paths = os.environ.get("PATH", "").split(os.pathsep)
            if os.path.normcase(str(directory)) not in {
                os.path.normcase(path) for path in existing_paths if path
            }:
                os.environ["PATH"] = (
                    str(directory)
                    + os.pathsep
                    + os.environ.get("PATH", "")
                )
            add_dll_directory = getattr(os, "add_dll_directory", None)
            if add_dll_directory is not None:
                _CUDA_DLL_DIRECTORY_HANDLES.append(
                    add_dll_directory(str(directory))
                )
            _CUDA_RUNTIME_DIRECTORY = directory
            return directory

    return None


@dataclass(frozen=True)
class TranscriptionResult:
    segments: list[dict[str, Any]]
    language: str
    model: str
    device: str
    compute_type: str
    duration_seconds: float
    stopped: bool = False


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
    if device == "cuda":
        _configure_windows_cuda_runtime()
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

    def release(self) -> None:
        """Called only under the shared runtime lease, with no active decode."""
        with self._lock:
            self._model = None
            self._device = None
            self._compute_type = None

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

    def _invalidate_model(self, model: Any) -> None:
        with self._lock:
            if self._model is model:
                self._model = None
                self._device = None
                self._compute_type = None

    @staticmethod
    def _decode(
        model: Any,
        audio_path: Path,
        diagnostic_callback: Callable[[dict[str, Any]], None] | None = None,
        vad_filter: bool = True,
        on_segment: Callable[[dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        stop_observed: list[bool] | None = None,
    ) -> tuple[list[dict[str, Any]], Any]:
        generated_segments, info = model.transcribe(
            str(audio_path), beam_size=5, vad_filter=vad_filter
        )
        normalized_segments: list[dict[str, Any]] = []
        previous_start = -1.0
        segment_iterator = iter(generated_segments)
        while True:
            if should_stop is not None and should_stop():
                if stop_observed is not None:
                    stop_observed[0] = True
                break
            try:
                segment = next(segment_iterator)
            except StopIteration:
                break
            raw_segment = {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            }
            if diagnostic_callback is not None:
                diagnostic_callback(
                    {
                        **raw_segment,
                        "id": getattr(segment, "id", None),
                        "seek": getattr(segment, "seek", None),
                        "avg_logprob": getattr(segment, "avg_logprob", None),
                        "no_speech_prob": getattr(
                            segment, "no_speech_prob", None
                        ),
                        "compression_ratio": getattr(
                            segment, "compression_ratio", None
                        ),
                    }
                )
            normalized = normalize_transcribed_segments([raw_segment])
            raw_start = float(raw_segment["start"])
            if raw_start < previous_start:
                raise ValueError(
                    "Whisper returned an invalid segment time range"
                )
            previous_start = raw_start
            if normalized:
                completed_segment = normalized[0]
                normalized_segments.append(completed_segment)
                if on_segment is not None:
                    on_segment(dict(completed_segment))
            if should_stop is not None and should_stop():
                if stop_observed is not None:
                    stop_observed[0] = True
                break
        return normalized_segments, info

    def transcribe(
        self,
        audio_path: Path,
        on_segment: Callable[[dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        *,
        retry_without_vad: bool = True,
        allow_empty: bool = False,
    ) -> TranscriptionResult:
        model = self._get_model()
        started = perf_counter()
        stop_observed = [False]
        try:
            segments, info = self._decode(
                model,
                audio_path,
                on_segment=on_segment,
                should_stop=should_stop,
                stop_observed=stop_observed,
            )
        except VideoExtractionError:
            raise
        except Exception as exc:
            if self.settings.device == "auto" and self._device == "cuda":
                self._invalidate_model(model)
                model = self._activate_cpu_fallback()
                try:
                    segments, info = self._decode(
                        model,
                        audio_path,
                        on_segment=on_segment,
                        should_stop=should_stop,
                        stop_observed=stop_observed,
                    )
                except Exception as fallback_exc:
                    self._invalidate_model(model)
                    raise VideoExtractionError(
                        502,
                        "whisper_transcription_failed",
                        "Local faster-whisper transcription failed.",
                    ) from fallback_exc
            else:
                self._invalidate_model(model)
                raise VideoExtractionError(
                    502,
                    "whisper_transcription_failed",
                    "Local faster-whisper transcription failed.",
                ) from exc

        duration = round(perf_counter() - started, 3)
        if (
            not segments
            and not stop_observed[0]
            and should_stop is not None
            and should_stop()
        ):
            stop_observed[0] = True
        if not segments and not stop_observed[0] and retry_without_vad:
            try:
                segments, retry_info = self._decode(
                    model,
                    audio_path,
                    vad_filter=False,
                    on_segment=on_segment,
                    should_stop=should_stop,
                    stop_observed=stop_observed,
                )
            except VideoExtractionError:
                raise
            except Exception as exc:
                self._invalidate_model(model)
                raise VideoExtractionError(
                    502,
                    "whisper_transcription_failed",
                    "Local faster-whisper transcription failed.",
                ) from exc
            if segments:
                info = retry_info

        if not segments and not stop_observed[0] and not allow_empty:
            raise VideoExtractionError(
                502,
                "whisper_empty_transcript",
                "Local faster-whisper produced no usable transcript segments.",
            )

        language = getattr(info, "language", None)
        if not isinstance(language, str) or not language.strip():
            if stop_observed[0] or (allow_empty and not segments):
                language = "und"
            else:
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
            stopped=stop_observed[0],
        )


TRANSCRIBER = WhisperTranscriber()


def transcribe_audio(
    audio_path: Path,
    on_segment: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    *,
    retry_without_vad: bool = True,
    allow_empty: bool = False,
) -> TranscriptionResult:
    from app.services.inference_runtime import inference_session

    with inference_session("whisper", TRANSCRIBER.release):
        return TRANSCRIBER.transcribe(
            audio_path,
            on_segment,
            should_stop,
            retry_without_vad=retry_without_vad,
            allow_empty=allow_empty,
        )
