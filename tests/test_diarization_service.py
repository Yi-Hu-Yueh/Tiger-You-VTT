from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import diarization
from app.services import audio as audio_service


def base_result() -> dict:
    return {
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "Hello"},
            {"start": 2.0, "end": 4.0, "text": "World"},
            {"start": 5.0, "end": 6.0, "text": "No overlap"},
        ],
        "txt": "Hello World No overlap",
        "vtt": "WEBVTT\n\n",
        "srt": "original",
    }


def test_normalizes_labels_in_first_appearance_order_and_applies_offset() -> None:
    regions = [
        diarization.SpeakerRegion(3.0, 4.0, "SPEAKER_07"),
        diarization.SpeakerRegion(0.0, 2.0, "SPEAKER_12"),
        diarization.SpeakerRegion(2.0, 3.0, "SPEAKER_07"),
    ]

    normalized = diarization.normalize_speakers(regions, offset=60.0)

    assert normalized == [
        diarization.SpeakerRegion(60.0, 62.0, "Speaker 1"),
        diarization.SpeakerRegion(62.0, 63.0, "Speaker 2"),
        diarization.SpeakerRegion(63.0, 64.0, "Speaker 2"),
    ]


def test_alignment_uses_greatest_overlap_and_unknown_when_none() -> None:
    regions = [
        diarization.SpeakerRegion(0.0, 1.5, "Speaker 1"),
        diarization.SpeakerRegion(1.5, 4.5, "Speaker 2"),
    ]

    aligned = diarization.align_speakers(base_result()["segments"], regions)

    assert [segment["speaker"] for segment in aligned] == [
        "Speaker 1",
        "Speaker 2",
        None,
    ]


def test_one_and_three_speaker_alignment() -> None:
    segments = [
        {"start": 0.0, "end": 1.0, "text": "One"},
        {"start": 1.0, "end": 2.0, "text": "Two"},
        {"start": 2.0, "end": 3.0, "text": "Three"},
    ]
    one = diarization.align_speakers(
        segments, [diarization.SpeakerRegion(0.0, 3.0, "Speaker 1")]
    )
    three = diarization.align_speakers(
        segments,
        [
            diarization.SpeakerRegion(0.0, 1.0, "Speaker 1"),
            diarization.SpeakerRegion(1.0, 2.0, "Speaker 2"),
            diarization.SpeakerRegion(2.0, 3.0, "Speaker 3"),
        ],
    )

    assert [item["speaker"] for item in one] == ["Speaker 1"] * 3
    assert [item["speaker"] for item in three] == [
        "Speaker 1",
        "Speaker 2",
        "Speaker 3",
    ]


def test_separate_media_have_independent_speaker_mappings() -> None:
    first = diarization.normalize_speakers(
        [
            diarization.SpeakerRegion(0.0, 1.0, "raw-A"),
            diarization.SpeakerRegion(1.0, 2.0, "raw-B"),
        ]
    )
    second = diarization.normalize_speakers(
        [
            diarization.SpeakerRegion(0.0, 1.0, "raw-B"),
            diarization.SpeakerRegion(1.0, 2.0, "raw-A"),
        ]
    )

    assert [item.speaker for item in first] == ["Speaker 1", "Speaker 2"]
    assert [item.speaker for item in second] == ["Speaker 1", "Speaker 2"]

def test_apply_diarization_preserves_original_outputs_and_adds_speaker_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    original = base_result()
    monkeypatch.setattr(
        diarization,
        "diarize_audio",
        lambda _path: (
            [
                diarization.SpeakerRegion(0.0, 2.0, "A"),
                diarization.SpeakerRegion(2.0, 4.0, "B"),
            ],
            1.2345,
        ),
    )

    result = diarization.apply_diarization(original, audio_path)

    assert result["txt"] == original["txt"]
    assert result["vtt"] == original["vtt"]
    assert result["srt"] == original["srt"]
    assert result["diarization_status"] == "completed"
    assert result["speaker_count"] == 2
    assert result["diarization_duration"] == 1.234
    assert result["speaker_txt"].splitlines() == [
        "Speaker 1：Hello",
        "Speaker 2：World",
        "Unknown：No overlap",
    ]
    assert "[Speaker 1] Hello" in result["speaker_vtt"]
    assert "00:00:02,000 --> 00:00:04,000" in result["speaker_srt"]
    assert original == base_result()


def test_apply_diarization_offsets_range_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "range.wav"
    audio_path.write_bytes(b"audio")
    result = base_result()
    result["segments"] = [{"start": 60.0, "end": 62.0, "text": "Range"}]
    monkeypatch.setattr(
        diarization,
        "diarize_audio",
        lambda _path: ([diarization.SpeakerRegion(0.0, 2.0, "A")], 0.1),
    )

    output = diarization.apply_diarization(
        result, audio_path, timestamp_offset=60.0
    )

    assert output["diarized_segments"][0]["speaker"] == "Speaker 1"
    assert output["diarized_segments"][0]["start"] == 60.0


def test_optional_failure_preserves_base_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    original = base_result()
    monkeypatch.setattr(
        diarization,
        "diarize_audio",
        lambda _path: (_ for _ in ()).throw(
            RuntimeError("diarization_model_load_failed")
        ),
    )

    result = diarization.apply_diarization(original, audio_path)

    assert result["diarization_status"] == "failed"
    assert result["diarization_error"] == {
        "code": "diarization_model_load_failed",
        "message": "The local speaker diarization model could not be loaded.",
    }
    assert result["segments"] == original["segments"]
    assert result["txt"] == original["txt"]
    serialized_error = str(result["diarization_error"])
    assert "HF_TOKEN" not in serialized_error
    assert str(tmp_path) not in serialized_error


def test_stop_before_diarization_does_not_invoke_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    called = False

    def fail_if_called(_path):
        nonlocal called
        called = True
        raise AssertionError("model must not run")

    monkeypatch.setattr(diarization, "diarize_audio", fail_if_called)
    result = diarization.apply_diarization(
        base_result(), audio_path, should_stop=lambda: True
    )

    assert called is False
    assert result["diarization_status"] == "stopped"


def test_stop_requested_during_diarization_finishes_safe_stage_then_stops_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    checks = iter([False, True])
    monkeypatch.setattr(
        diarization,
        "diarize_audio",
        lambda _path: ([diarization.SpeakerRegion(0.0, 2.0, "A")], 0.2),
    )

    result = diarization.apply_diarization(
        base_result(), audio_path, should_stop=lambda: next(checks)
    )

    assert result["diarization_status"] == "completed"
    assert result["_stopped"] is True


def test_successful_pipeline_load_is_lazy_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pyannote.audio import Pipeline

    calls = []
    fake = SimpleNamespace(to=lambda _device: None)
    monkeypatch.setattr(diarization, "_pipeline", None)
    monkeypatch.setattr(
        Pipeline,
        "from_pretrained",
        staticmethod(lambda model, token: calls.append((model, token)) or fake),
    )

    assert diarization._load_pipeline() is fake
    assert diarization._load_pipeline() is fake
    assert calls == [(diarization.DIARIZATION_SETTINGS.model, True)]


def test_failed_pipeline_load_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pyannote.audio import Pipeline

    attempts = 0

    def fail(_model, token):
        nonlocal attempts
        attempts += 1
        assert token is True
        raise OSError("not available")

    monkeypatch.setattr(diarization, "_pipeline", None)
    monkeypatch.setattr(Pipeline, "from_pretrained", staticmethod(fail))

    with pytest.raises(RuntimeError, match="diarization_model_load_failed"):
        diarization._load_pipeline()
    with pytest.raises(RuntimeError, match="diarization_model_load_failed"):
        diarization._load_pipeline()
    assert attempts == 2


def test_disabled_audio_path_never_invokes_diarization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "speech.wav"
    media_path.write_bytes(b"audio")
    monkeypatch.setattr(
        audio_service,
        "probe_audio",
        lambda _path: SimpleNamespace(
            duration=10.0, has_audio=True, has_video=False
        ),
    )
    monkeypatch.setattr(
        audio_service,
        "_transcribe_audio",
        lambda *_args: SimpleNamespace(
            segments=[{"start": 0.0, "end": 1.0, "text": "Hello"}],
            language="en",
            model="large-v3",
            device="cuda",
            compute_type="int8_float32",
            duration_seconds=0.5,
            stopped=False,
        ),
    )
    monkeypatch.setattr(
        diarization,
        "apply_diarization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled path must not invoke diarization")
        ),
    )

    result = audio_service.process_uploaded_audio(media_path, "speech.wav")

    assert "diarization_enabled" not in result
    assert result["txt"] == "Hello"


def test_enabled_audio_path_runs_diarization_after_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "speech.wav"
    media_path.write_bytes(b"audio")
    events = []
    monkeypatch.setattr(
        audio_service,
        "probe_audio",
        lambda _path: SimpleNamespace(
            duration=10.0, has_audio=True, has_video=False
        ),
    )

    def transcribe(*_args):
        events.append("whisper")
        return SimpleNamespace(
            segments=[{"start": 0.0, "end": 1.0, "text": "Hello"}],
            language="en",
            model="large-v3",
            device="cuda",
            compute_type="int8_float32",
            duration_seconds=0.5,
            stopped=False,
        )

    def apply(result, path, **_kwargs):
        events.append("diarization")
        assert path == media_path
        return {**result, "diarization_enabled": True}

    monkeypatch.setattr(audio_service, "_transcribe_audio", transcribe)
    monkeypatch.setattr(diarization, "apply_diarization", apply)

    result = audio_service.process_uploaded_audio(
        media_path, "speech.wav", enable_diarization=True
    )

    assert events == ["whisper", "diarization"]
    assert result["diarization_enabled"] is True


def test_range_diarization_temporary_audio_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "speech.wav"
    media_path.write_bytes(b"audio")
    created_directory: Path | None = None
    monkeypatch.setattr(
        audio_service,
        "probe_audio",
        lambda _path: SimpleNamespace(
            duration=180.0, has_audio=True, has_video=False
        ),
    )

    def create_clip(_source, destination, _start, _end):
        nonlocal created_directory
        created_directory = destination.parent
        destination.write_bytes(b"range")
        return destination

    monkeypatch.setattr(audio_service, "create_range_audio_clip", create_clip)
    monkeypatch.setattr(
        audio_service,
        "_transcribe_audio",
        lambda *_args: SimpleNamespace(
            segments=[{"start": 0.0, "end": 1.0, "text": "Range"}],
            language="en",
            model="large-v3",
            device="cuda",
            compute_type="int8_float32",
            duration_seconds=0.5,
            stopped=False,
        ),
    )
    monkeypatch.setattr(
        diarization,
        "apply_diarization",
        lambda result, path, **_kwargs: (
            {**result, "diarization_enabled": path.is_file()}
        ),
    )

    result = audio_service.process_uploaded_audio(
        media_path,
        "speech.wav",
        start_time="0:1",
        end_time="0:2",
        enable_diarization=True,
    )

    assert result["diarization_enabled"] is True
    assert created_directory is not None
    assert not created_directory.exists()
