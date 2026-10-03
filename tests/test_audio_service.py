import asyncio
import json
from pathlib import Path

import pytest

from app.config import VideoUploadSettings
from app.services.audio import (
    SUPPORTED_AUDIO_EXTENSIONS,
    _transcribe_audio,
    probe_audio,
    process_uploaded_audio,
    safe_audio_upload_name,
    store_audio_upload,
)
from app.services.errors import VideoExtractionError
from app.services.video import MediaInfo, MediaStream
from app.services.whisper import TranscriptionResult


def transcription(*, device: str = "cuda", stopped: bool = False):
    return TranscriptionResult(
        segments=[
            {"start": 0.5, "end": 3.2, "text": "Hello"},
            {"start": 3.2, "end": 4.0, "text": "world"},
        ],
        language="en",
        model="large-v3",
        device=device,
        compute_type="int8_float32" if device == "cuda" else "int8",
        duration_seconds=1.25,
        stopped=stopped,
    )


@pytest.mark.parametrize(
    "extension", sorted(SUPPORTED_AUDIO_EXTENSIONS)
)
def test_supported_audio_extensions_are_accepted(extension: str) -> None:
    assert safe_audio_upload_name(f"speech{extension.upper()}") == (
        f"speech{extension.upper()}",
        extension,
    )


def test_audio_filename_is_sanitized_and_unsupported_format_rejected() -> None:
    assert safe_audio_upload_name("../../secret/speech.mp3") == (
        "speech.mp3",
        ".mp3",
    )
    assert safe_audio_upload_name(r"..\..\secret\speech.wav") == (
        "speech.wav",
        ".wav",
    )
    with pytest.raises(VideoExtractionError) as error:
        safe_audio_upload_name("payload.exe")
    assert error.value.code == "unsupported_audio_format"


def test_audio_upload_streams_in_chunks_and_enforces_shared_limit(tmp_path) -> None:
    class Upload:
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.sizes = []

        async def read(self, size):
            self.sizes.append(size)
            return self.chunks.pop(0)

    upload = Upload([b"abc", b"def", b""])
    destination = tmp_path / "upload.mp3"
    count = asyncio.run(
        store_audio_upload(
            upload,
            destination,
            VideoUploadSettings(max_bytes=10, chunk_bytes=4),
        )
    )
    assert count == 6
    assert upload.sizes == [4, 4, 4]
    assert destination.read_bytes() == b"abcdef"

    with pytest.raises(VideoExtractionError) as error:
        asyncio.run(
            store_audio_upload(
                Upload([b"1234", b"5678", b""]),
                tmp_path / "too-large.mp3",
                VideoUploadSettings(max_bytes=6, chunk_bytes=4),
            )
        )
    assert error.value.code == "upload_too_large"


def test_empty_audio_upload_is_invalid(tmp_path) -> None:
    class EmptyUpload:
        async def read(self, _size):
            return b""

    with pytest.raises(VideoExtractionError) as error:
        asyncio.run(store_audio_upload(EmptyUpload(), tmp_path / "empty.wav"))
    assert error.value.code == "invalid_audio_file"


def test_probe_audio_requires_audio_only_stream_and_duration(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.audio.probe_media_info",
        lambda _path, **_kwargs: MediaInfo(
            12.5, (MediaStream(0, "audio", "mp3"),)
        ),
    )
    media = probe_audio(Path("speech.mp3"))
    assert media.has_audio and not media.has_video
    assert media.duration == 12.5

    monkeypatch.setattr(
        "app.services.audio.probe_media_info",
        lambda _path, **_kwargs: MediaInfo(
            12.5, (MediaStream(0, "video", "h264"),)
        ),
    )
    with pytest.raises(VideoExtractionError) as error:
        probe_audio(Path("silent.mp3"))
    assert error.value.code == "no_audio_available"

    monkeypatch.setattr(
        "app.services.audio.probe_media_info",
        lambda _path, **_kwargs: MediaInfo(
            12.5,
            (
                MediaStream(0, "video", "h264"),
                MediaStream(1, "audio", "aac"),
            ),
        ),
    )
    with pytest.raises(VideoExtractionError) as error:
        probe_audio(Path("renamed-video.mp3"))
    assert error.value.code == "invalid_audio_file"

    monkeypatch.setattr(
        "app.services.audio.probe_media_info",
        lambda _path, **_kwargs: MediaInfo(
            None, (MediaStream(0, "audio", "flac"),)
        ),
    )
    with pytest.raises(VideoExtractionError) as error:
        probe_audio(Path("unknown.flac"))
    assert error.value.code == "invalid_audio_file"


def test_corrupt_audio_is_controlled_by_structured_probe(tmp_path, monkeypatch) -> None:
    class Completed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(
        "app.services.video.subprocess.run", lambda *_args, **_kwargs: Completed()
    )
    with pytest.raises(VideoExtractionError) as error:
        probe_audio(tmp_path / "corrupt.mp3")
    assert error.value.code == "invalid_audio_file"


def test_no_audio_stream_never_invokes_whisper(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.audio.probe_audio",
        lambda _path: (_ for _ in ()).throw(
            VideoExtractionError(
                422, "no_audio_available", "No usable audio stream."
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio",
        lambda *_args: pytest.fail("Whisper must not run without audio"),
    )
    with pytest.raises(VideoExtractionError) as error:
        process_uploaded_audio(tmp_path / "silent.wav", "silent.wav")
    assert error.value.code == "no_audio_available"


def test_direct_audio_reuses_existing_whisper_and_propagates_metadata(
    monkeypatch, tmp_path
) -> None:
    media = MediaInfo(10.0, (MediaStream(0, "audio", "mp3"),))
    source = tmp_path / "speech.mp3"
    observed = []
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio",
        lambda path, *_args: observed.append(path) or transcription(),
    )

    result = process_uploaded_audio(source, "speech.mp3")

    assert observed == [source]
    assert result["type"] == "transcribed"
    assert result["selection_mode"] == "direct"
    assert result["language"] == "en"
    assert result["transcription_model"] == "large-v3"
    assert result["transcription_device"] == "cuda"
    assert result["transcription_compute_type"] == "int8_float32"
    assert result["txt"] == "Hello,world"
    assert result["vtt"].startswith("WEBVTT\n\n00:00:00.500")
    assert result["srt"].startswith("1\n00:00:00,500")


def test_direct_audio_propagates_cpu_fallback_metadata(monkeypatch, tmp_path) -> None:
    media = MediaInfo(10.0, (MediaStream(0, "audio", "wav"),))
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio",
        lambda *_args: transcription(device="cpu"),
    )
    result = process_uploaded_audio(tmp_path / "speech.wav", "speech.wav")
    assert result["transcription_device"] == "cpu"
    assert result["transcription_compute_type"] == "int8"


def test_audio_adapter_calls_single_existing_whisper_service(monkeypatch) -> None:
    expected = transcription(device="cpu")
    calls = []
    monkeypatch.setattr(
        "app.services.whisper.transcribe_audio",
        lambda path, on_segment=None, should_stop=None: (
            calls.append((path, on_segment, should_stop)) or expected
        ),
    )
    callback = lambda _segment: None
    stop = lambda: False

    result = _transcribe_audio(Path("speech.wav"), callback, stop)

    assert result is expected
    assert calls == [(Path("speech.wav"), callback, stop)]


def test_audio_range_clips_offsets_once_and_cleans(monkeypatch, tmp_path) -> None:
    media = MediaInfo(300.0, (MediaStream(0, "audio", "aac"),))
    clip_directory = None
    partial = []
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)

    def clip(_source, output, start, end):
        nonlocal clip_directory
        clip_directory = output.parent
        assert (start, end) == (60.0, 120.0)
        output.write_bytes(b"clip")
        return output

    def transcribe(path, on_segment, _should_stop):
        assert path.name == "range.wav"
        on_segment({"start": 0.5, "end": 3.2, "text": "Hello"})
        return transcription()

    monkeypatch.setattr("app.services.audio.create_range_audio_clip", clip)
    monkeypatch.setattr("app.services.audio._transcribe_audio", transcribe)

    result = process_uploaded_audio(
        tmp_path / "speech.aac",
        "speech.aac",
        start_time="0:1",
        end_time="0:2",
        on_segment=partial.append,
        should_stop=lambda: False,
    )

    assert partial == [{"start": 60.5, "end": 63.2, "text": "Hello"}]
    assert result["segments"][0]["start"] == 60.5
    assert result["segments"][1]["start"] == 63.2
    assert "00:01:00.500" in result["vtt"]
    assert clip_directory is not None and not clip_directory.exists()


def test_blank_range_transcribes_original_audio_without_clip(monkeypatch, tmp_path) -> None:
    media = MediaInfo(10.0, (MediaStream(0, "audio", "wav"),))
    source = tmp_path / "speech.wav"
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)
    monkeypatch.setattr(
        "app.services.audio.create_range_audio_clip",
        lambda *_args: pytest.fail("full audio must not create a clip"),
    )
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio",
        lambda path, *_args: transcription() if path == source else None,
    )
    result = process_uploaded_audio(source, "speech.wav")
    assert result["range_start"] == 0.0
    assert result["range_end"] == 10.0


def test_short_audio_untouched_default_clamps_to_duration(monkeypatch, tmp_path) -> None:
    media = MediaInfo(120.0, (MediaStream(0, "audio", "flac"),))
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)
    monkeypatch.setattr(
        "app.services.audio.create_range_audio_clip",
        lambda source, _output, _start, _end: source,
    )
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio", lambda *_args: transcription()
    )
    result = process_uploaded_audio(
        tmp_path / "short.flac",
        "short.flac",
        start_time="0:0",
        end_time="0:10",
        end_time_is_default=True,
    )
    assert result["range_end"] == 120.0


def test_audio_invalid_range_is_rejected_before_whisper(monkeypatch, tmp_path) -> None:
    media = MediaInfo(120.0, (MediaStream(0, "audio", "wav"),))
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio",
        lambda *_args: pytest.fail("Whisper must not run for an invalid range"),
    )
    with pytest.raises(VideoExtractionError) as error:
        process_uploaded_audio(
            tmp_path / "speech.wav",
            "speech.wav",
            start_time="0:1",
            end_time="0:3",
        )
    assert error.value.code == "invalid_time_range"


def test_audio_range_clip_cleans_after_failure(monkeypatch, tmp_path) -> None:
    media = MediaInfo(300.0, (MediaStream(0, "audio", "ogg"),))
    clip_directory = None
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)

    def clip(_source, output, _start, _end):
        nonlocal clip_directory
        clip_directory = output.parent
        output.write_bytes(b"clip")
        return output

    def fail(*_args):
        raise VideoExtractionError(502, "whisper_transcription_failed", "failed")

    monkeypatch.setattr("app.services.audio.create_range_audio_clip", clip)
    monkeypatch.setattr("app.services.audio._transcribe_audio", fail)
    with pytest.raises(VideoExtractionError):
        process_uploaded_audio(
            tmp_path / "speech.ogg",
            "speech.ogg",
            start_time="0:1",
            end_time="0:2",
        )
    assert clip_directory is not None and not clip_directory.exists()


def test_stop_before_first_segment_skips_whisper(monkeypatch, tmp_path) -> None:
    media = MediaInfo(10.0, (MediaStream(0, "audio", "opus"),))
    monkeypatch.setattr("app.services.audio.probe_audio", lambda _path: media)
    monkeypatch.setattr(
        "app.services.audio._transcribe_audio",
        lambda *_args: pytest.fail("Whisper must not run after stop"),
    )
    result = process_uploaded_audio(
        tmp_path / "speech.opus",
        "speech.opus",
        should_stop=lambda: True,
    )
    assert result["_stopped"] is True
    assert result["segment_count"] == 0
    assert result["txt"] == ""
    assert result["vtt"] == "WEBVTT\n\n"
    assert result["srt"] == ""
