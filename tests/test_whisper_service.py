from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from app.services import whisper as whisper_service
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


def test_successful_cuda_model_is_cached_and_reused() -> None:
    model = FakeModel()
    calls: list[tuple[str, str]] = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return model

    transcriber = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8_float32"} if device == "cuda" else {"int8"}
        ),
    )

    first = transcriber.transcribe(Path("audio.webm"))
    second = transcriber.transcribe(Path("audio.webm"))

    assert calls == [("cuda", "int8_float32")]
    assert model.calls == 2
    assert first.device == second.device == "cuda"


def test_failed_cuda_model_is_replaced_by_cached_cpu_fallback() -> None:
    calls: list[tuple[str, str]] = []
    cuda_model = FakeModel()
    cuda_model.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("CUDA runtime unavailable")
    )
    cpu_model = FakeModel()

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return cuda_model if device == "cuda" else cpu_model

    transcriber = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8_float32"} if device == "cuda" else {"int8"}
        ),
    )

    first = transcriber.transcribe(Path("audio.webm"))
    second = transcriber.transcribe(Path("audio.webm"))

    assert calls == [("cuda", "int8_float32"), ("cpu", "int8")]
    assert cpu_model.calls == 2
    assert first.device == second.device == "cpu"


def test_failed_cached_runtime_is_invalidated_before_next_job() -> None:
    broken = FakeModel()
    broken.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("runtime poisoned")
    )
    healthy = FakeModel()
    models = iter((broken, healthy))
    factory_calls = []

    def factory(_model: str, device: str, compute_type: str):
        factory_calls.append((device, compute_type))
        return next(models)

    transcriber = WhisperTranscriber(
        settings("cpu", "int8"),
        model_factory=factory,
        compute_type_provider=lambda _device: {"int8"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))
    assert error.value.code == "whisper_transcription_failed"
    assert transcriber.loaded is False

    result = transcriber.transcribe(Path("audio.webm"))

    assert result.language == "zh"
    assert factory_calls == [("cpu", "int8"), ("cpu", "int8")]


def test_failed_cuda_and_cpu_fallback_load_do_not_poison_next_job() -> None:
    broken_cuda = FakeModel()
    broken_cuda.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("cuda runtime failed")
    )
    healthy_cuda = FakeModel()
    cuda_models = iter((broken_cuda, healthy_cuda))
    calls = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        if device == "cpu":
            raise RuntimeError("cpu model load failed")
        return next(cuda_models)

    transcriber = WhisperTranscriber(
        settings(),
        model_factory=factory,
        cuda_counter=lambda: 1,
        compute_type_provider=lambda device: (
            {"int8_float32"} if device == "cuda" else {"int8"}
        ),
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))
    assert error.value.code == "whisper_model_load_failed"
    assert transcriber.loaded is False

    result = transcriber.transcribe(Path("audio.webm"))

    assert result.device == "cuda"
    assert calls == [
        ("cuda", "int8_float32"),
        ("cpu", "int8"),
        ("cuda", "int8_float32"),
    ]


def test_explicit_cpu_never_probes_cuda() -> None:
    calls: list[tuple[str, str]] = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return FakeModel()

    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=factory,
        cuda_counter=lambda: pytest.fail("CUDA should not be probed"),
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(Path("audio.webm"))

    assert calls == [("cpu", "int8")]
    assert result.device == "cpu"


def test_explicit_cuda_inference_failure_is_controlled_without_cpu_fallback() -> None:
    model = FakeModel()
    model.transcribe = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("CUDA execution failed")
    )
    calls: list[tuple[str, str]] = []

    def factory(_model: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return model

    transcriber = WhisperTranscriber(
        settings("cuda", "int8_float32"),
        model_factory=factory,
        compute_type_provider=lambda _device: {"int8_float32"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_transcription_failed"
    assert calls == [("cuda", "int8_float32")]


def test_windows_cuda_runtime_uses_existing_venv_dlls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_directory = tmp_path / "torch" / "lib"
    runtime_directory.mkdir(parents=True)
    for dll_name in (
        "cublas64_12.dll",
        "cublasLt64_12.dll",
    ):
        (runtime_directory / dll_name).touch()
    handle = object()
    calls: list[str] = []

    monkeypatch.setattr(whisper_service, "_CUDA_RUNTIME_DIRECTORY", None)
    monkeypatch.setattr(whisper_service, "_CUDA_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setattr(
        whisper_service,
        "_cuda_runtime_candidates",
        lambda: [runtime_directory],
    )
    monkeypatch.setattr(
        whisper_service.os,
        "add_dll_directory",
        lambda directory: calls.append(directory) or handle,
    )
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")

    selected = whisper_service._configure_windows_cuda_runtime()

    assert selected == runtime_directory.resolve()
    assert calls == [str(runtime_directory.resolve())]
    assert whisper_service._CUDA_DLL_DIRECTORY_HANDLES == [handle]
    assert whisper_service.os.environ["PATH"].split(whisper_service.os.pathsep)[
        0
    ] == str(runtime_directory.resolve())


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


def test_raw_segment_diagnostic_precedes_normalization_and_does_not_split() -> None:
    raw_segment = SimpleNamespace(
        id=17,
        seek=6000,
        start=71.14,
        end=672.7,
        text="いいからさ、入ってるとこ見せてよ",
        avg_logprob=-0.42,
        no_speech_prob=0.13,
        compression_ratio=1.37,
    )
    model = FakeModel(segments=[raw_segment], language="ja")
    diagnostics: list[dict[str, object]] = []

    segments, info = WhisperTranscriber._decode(
        model, Path("audio.webm"), diagnostics.append
    )

    assert diagnostics == [
        {
            "start": 71.14,
            "end": 672.7,
            "text": "いいからさ、入ってるとこ見せてよ",
            "id": 17,
            "seek": 6000,
            "avg_logprob": -0.42,
            "no_speech_prob": 0.13,
            "compression_ratio": 1.37,
        }
    ]
    assert segments == [
        {
            "start": 71.14,
            "end": 672.7,
            "text": "いいからさ、入ってるとこ見せてよ",
        }
    ]
    assert info.language == "ja"


def test_empty_vad_result_retries_once_without_vad_and_preserves_output() -> None:
    class VadRetryModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def transcribe(self, audio_path: str, **kwargs):
            assert audio_path.endswith("audio.webm")
            self.calls.append(kwargs)
            if kwargs["vad_filter"]:
                return iter([]), SimpleNamespace(language="ko")
            return iter(
                [SimpleNamespace(start=0.33, end=0.76, text=" はい！ ")]
            ), SimpleNamespace(language="ja")

    model = VadRetryModel()
    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(Path("audio.webm"))

    assert model.calls == [
        {"beam_size": 5, "vad_filter": True},
        {"beam_size": 5, "vad_filter": False},
    ]
    assert result.language == "ja"
    assert result.segments == [
        {"start": 0.33, "end": 0.76, "text": "はい！"}
    ]


def test_empty_vad_result_and_empty_retry_remain_controlled() -> None:
    class EmptyModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def transcribe(self, _audio_path: str, **kwargs):
            self.calls.append(kwargs)
            return iter([]), SimpleNamespace(language="ja")

    model = EmptyModel()
    transcriber = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_empty_transcript"
    assert model.calls == [
        {"beam_size": 5, "vad_filter": True},
        {"beam_size": 5, "vad_filter": False},
    ]


def test_live_empty_chunk_is_allowed_without_no_vad_retry() -> None:
    class SilentModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def transcribe(self, _audio_path: str, **kwargs):
            self.calls.append(kwargs)
            return iter([]), SimpleNamespace(language=None)

    model = SilentModel()
    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(
        Path("audio.webm"), retry_without_vad=False, allow_empty=True
    )

    assert model.calls == [{"beam_size": 5, "vad_filter": True}]
    assert result.segments == []
    assert result.language == "und"
    assert result.stopped is False


def test_stop_before_first_segment_does_not_trigger_vad_retry() -> None:
    class LazyModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.generator_consumed = False

        def transcribe(self, _audio_path: str, **kwargs):
            self.calls.append(kwargs)

            def segments():
                self.generator_consumed = True
                yield SimpleNamespace(start=0.0, end=1.0, text="Too late")

            return segments(), SimpleNamespace(language="en")

    model = LazyModel()
    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(Path("audio.webm"), should_stop=lambda: True)

    assert result.stopped is True
    assert result.segments == []
    assert model.generator_consumed is False
    assert model.calls == [{"beam_size": 5, "vad_filter": True}]


def test_stop_arriving_between_empty_attempt_and_retry_prevents_retry() -> None:
    calls: list[dict[str, object]] = []
    stop_checks = 0

    class EmptyModel:
        def transcribe(self, _audio_path: str, **kwargs):
            calls.append(kwargs)
            return iter([]), SimpleNamespace(language="en")

    def should_stop() -> bool:
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 2

    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: EmptyModel(),
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(Path("audio.webm"), should_stop=should_stop)

    assert result.stopped is True
    assert result.segments == []
    assert calls == [{"beam_size": 5, "vad_filter": True}]


def test_stop_after_completed_segments_preserves_them_without_restart() -> None:
    stop_requested = Event()
    observed: list[dict[str, object]] = []

    class SegmentModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def transcribe(self, _audio_path: str, **kwargs):
            self.calls.append(kwargs)
            return iter(
                [
                    SimpleNamespace(start=0.0, end=1.0, text="A"),
                    SimpleNamespace(start=1.0, end=2.0, text="B"),
                    SimpleNamespace(start=2.0, end=3.0, text="C"),
                ]
            ), SimpleNamespace(language="en")

    def on_segment(segment: dict[str, object]) -> None:
        observed.append(segment)
        if len(observed) == 1:
            stop_requested.set()

    model = SegmentModel()
    result = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    ).transcribe(Path("audio.webm"), on_segment, stop_requested.is_set)

    assert result.stopped is True
    assert result.segments == [
        {"start": 0.0, "end": 1.0, "text": "A"},
    ]
    assert observed == result.segments
    assert model.calls == [{"beam_size": 5, "vad_filter": True}]


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
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("decoder internals")

    model.transcribe = fail
    transcriber = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_transcription_failed"
    assert "decoder internals" not in error.value.message
    assert calls == 1


def test_empty_transcript_is_controlled() -> None:
    model = FakeModel(segments=[SimpleNamespace(start=0, end=1, text=" ")])
    model.transcribe = lambda *_args, **_kwargs: (
        iter(model.segments),
        SimpleNamespace(language="zh"),
    )
    transcriber = WhisperTranscriber(
        settings("cpu"),
        model_factory=lambda *_args: model,
        compute_type_provider=lambda _device: {"int8"},
    )

    with pytest.raises(VideoExtractionError) as error:
        transcriber.transcribe(Path("audio.webm"))

    assert error.value.code == "whisper_empty_transcript"
