from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import WhisperSettings
from app.services.whisper import WhisperTranscriber
from app.services.youtube import VideoExtractionError


class FakeModel:
    def __init__(self, segments=None, language: str = "zh") -> None:
        self.segments = segments or [
            SimpleNamespace(start=0.0, end=1.25, text="  你好，世界  ")
        ]
        self.language = language
        self.calls = 0

    def transcribe(self, audio_path: str, **kwargs):
        self.calls += 1
        assert audio_path.endswith("audio.webm")
        assert kwargs == {"beam_size": 5, "vad_filter": True}
        return iter(self.segments), SimpleNamespace(language=self.language)


def settings(device: str = "auto", compute_type: str = "auto") -> WhisperSettings:
    return WhisperSettings("large-v3", device, compute_type)


def test_model_is_loaded_lazily_and_reused() -> None:
    model = FakeModel()
    factory_calls: list[tuple[str, str, str]] = []

    def factory(model_name: str, device: str, compute_type: str):
        factory_calls.append((model_name, device, compute_type))
        return model

    transcriber = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 0,
        compute_type_provider=lambda _device: {"int8"},
    )
    assert transcriber.loaded is False
    assert factory_calls == []

    first = transcriber.transcribe(Path("audio.webm"))
    second = transcriber.transcribe(Path("audio.webm"))

    assert transcriber.loaded is True
    assert factory_calls == [("large-v3", "cpu", "int8")]
    assert model.calls == 2
    assert first.language == second.language == "zh"


def test_cuda_is_selected_only_after_successful_model_initialization() -> None:
    calls: list[tuple[str, str]] = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return FakeModel()

    result = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8_float16"} if device == "cuda" else {"int8"}
        ),
    ).transcribe(Path("audio.webm"))

    assert calls == [("cuda", "int8_float16")]
    assert result.device == "cuda"
    assert result.compute_type == "int8_float16"


def test_auto_device_falls_back_to_cpu_when_cuda_initialization_fails() -> None:
    calls: list[tuple[str, str]] = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        if device == "cuda":
            raise RuntimeError("CUDA runtime unavailable")
        return FakeModel()

    result = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8_float16"} if device == "cuda" else {"int8"}
        ),
    ).transcribe(Path("audio.webm"))

    assert calls == [("cuda", "int8_float16"), ("cpu", "int8")]
    assert result.device == "cpu"
    assert result.compute_type == "int8"


def test_auto_device_falls_back_to_cpu_when_cuda_inference_fails() -> None:
    calls: list[tuple[str, str]] = []
    cuda_model = FakeModel()
    cuda_model.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("CUDA runtime unavailable")
    )
    cpu_model = FakeModel()

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return cuda_model if device == "cuda" else cpu_model

    result = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8_float32"} if device == "cuda" else {"int8"}
        ),
    ).transcribe(Path("audio.webm"))

    assert calls == [("cuda", "int8_float32"), ("cpu", "int8")]
    assert result.device == "cpu"
    assert result.compute_type == "int8"
    assert cpu_model.calls == 1


def test_auto_device_uses_supported_cuda_compute_type() -> None:
    calls: list[tuple[str, str]] = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return FakeModel()

    result = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8", "int8_float32", "float32"}
            if device == "cuda"
            else {"int8"}
        ),
    ).transcribe(Path("audio.webm"))

    assert calls == [("cuda", "int8_float32")]
    assert result.device == "cuda"
    assert result.compute_type == "int8_float32"


def test_detected_language_timestamps_empty_filter_and_unicode_are_preserved() -> None:
    model = FakeModel(
        segments=[
            SimpleNamespace(start=0.25, end=1.75, text=" 技術課程 "),
            SimpleNamespace(start=1.75, end=2.0, text="  "),
        ],
        language="zh",
    )
    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        cuda_counter=lambda: 0,
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(Path("audio.webm"))

    assert result.language == "zh"
    assert result.segments == [
        {"start": 0.25, "end": 1.75, "text": "技術課程"}
    ]


def test_model_load_failure_is_controlled() -> None:
    def fail(*_args):
        raise OSError("private model path")

    transcriber = WhisperTranscriber(
        settings("cpu"),
        model_factory=fail,
        cuda_counter=lambda: 0,
        compute_type_provider=lambda _device: {"int8"},
    )
    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_model_load_failed"
    assert "private model path" not in error.value.message


def test_transcription_failure_is_controlled() -> None:
    model = FakeModel()
    model.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("decoder internals")
    )
    transcriber = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_transcription_failed"
    assert "decoder internals" not in error.value.message


def test_empty_transcript_is_controlled() -> None:
    model = FakeModel(segments=[SimpleNamespace(start=0, end=1, text=" ")])
    transcriber = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_empty_transcript"
