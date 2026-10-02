import asyncio
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.config import VideoUploadSettings
from app.services.errors import VideoExtractionError
from app.services.video import (
    MediaInfo,
    MediaStream,
    extract_embedded_vtt,
    probe_media,
    process_uploaded_video,
    safe_upload_name,
    select_embedded_subtitle,
    store_upload,
)
from app.services.whisper import TranscriptionResult


def completed(payload: dict[str, object], returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr="private diagnostic",
    )


def test_probe_parses_video_audio_subtitles_language_default_and_duration() -> None:
    payload = {
        "format": {"duration": "12.750"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "zh-TW"},
                "disposition": {"default": 1},
            },
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "tags": {"language": "eng"},
            },
        ],
    }

    media = probe_media(
        Path("video.mkv"), runner=lambda *_args, **_kwargs: completed(payload)
    )

    assert media.duration == 12.75
    assert media.has_video is True
    assert media.has_audio is True
    assert [stream.index for stream in media.text_subtitles] == [2]
    assert media.streams[2].language == "zh-TW"
    assert media.streams[2].is_default is True
    assert media.streams[3].is_text_subtitle is False


def test_probe_failure_and_missing_ffprobe_are_controlled() -> None:
    with pytest.raises(VideoExtractionError) as invalid:
        probe_media(
            Path("corrupt.mp4"),
            runner=lambda *_args, **_kwargs: completed({}, returncode=1),
        )
    assert invalid.value.code == "invalid_video_file"
    assert "private diagnostic" not in invalid.value.message

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("private path")

    with pytest.raises(VideoExtractionError) as unavailable:
        probe_media(Path("video.mp4"), runner=missing)
    assert unavailable.value.code == "media_probe_failed"
    assert "private path" not in unavailable.value.message


def test_probe_requires_a_real_video_stream() -> None:
    payload = {
        "format": {"duration": "1.0"},
        "streams": [
            {"index": 0, "codec_type": "audio", "codec_name": "aac"}
        ],
    }
    with pytest.raises(VideoExtractionError) as error:
        probe_media(
            Path("not-video.mp4"),
            runner=lambda *_args, **_kwargs: completed(payload),
        )
    assert error.value.code == "invalid_video_file"


def test_default_text_subtitle_precedes_language_priority() -> None:
    media = MediaInfo(
        10.0,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(4, "subtitle", "subrip", "zh-TW"),
            MediaStream(5, "subtitle", "ass", "en", True),
        ),
    )

    assert select_embedded_subtitle(media).index == 5


def test_language_priority_and_stream_order_are_deterministic() -> None:
    media = MediaInfo(
        10.0,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(8, "subtitle", "subrip", None),
            MediaStream(7, "subtitle", "subrip", "en"),
            MediaStream(6, "subtitle", "subrip", "zh"),
            MediaStream(5, "subtitle", "subrip", "zh-Hant"),
        ),
    )
    assert select_embedded_subtitle(media).index == 5

    unknown = MediaInfo(
        10.0,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(9, "subtitle", "subrip", None),
            MediaStream(3, "subtitle", "subrip", None),
        ),
    )
    assert select_embedded_subtitle(unknown).index == 3


def test_embedded_path_generates_outputs_without_whisper(monkeypatch, tmp_path) -> None:
    media = MediaInfo(
        8.5,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(1, "audio", "aac"),
            MediaStream(2, "subtitle", "subrip", None, True),
        ),
    )
    source_vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n你好。\n\n"
        "00:00:01.000 --> 00:00:02.000\nSecond.\n"
    )
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)
    monkeypatch.setattr(
        "app.services.video.extract_embedded_vtt",
        lambda *_args: source_vtt,
    )
    monkeypatch.setattr(
        "app.services.video._transcribe_local_media",
        lambda _path: pytest.fail("Whisper must not run"),
    )

    result = process_uploaded_video(
        tmp_path / "upload.mkv", "lesson.mkv", tmp_path
    )

    assert result["filename"] == "lesson.mkv"
    assert result["language"] == "und"
    assert result["type"] == "embedded"
    assert result["selection_mode"] == "auto"
    assert result["duration"] == 8.5
    assert result["segment_count"] == 2
    assert result["segments"] == [
        {"start": 0.0, "end": 1.0, "text": "你好。"},
        {"start": 1.0, "end": 2.0, "text": "Second."},
    ]
    assert result["vtt"].startswith("WEBVTT\n\n00:00:00.000")
    assert result["txt"] == "你好。,Second."
    assert result["srt"].startswith("1\n00:00:00,000")
    assert "\n\n2\n00:00:01,000" in result["srt"]


@pytest.mark.parametrize("subtitle_codec", [None, "hdmv_pgs_subtitle"])
def test_audio_path_reuses_whisper_for_no_text_or_bitmap_subtitle(
    monkeypatch, tmp_path, subtitle_codec
) -> None:
    streams = [
        MediaStream(0, "video", "h264"),
        MediaStream(1, "audio", "aac"),
    ]
    if subtitle_codec:
        streams.append(MediaStream(2, "subtitle", subtitle_codec, "zh"))
    media = MediaInfo(4.0, tuple(streams))
    transcription = TranscriptionResult(
        segments=[
            {"start": 0.0, "end": 1.0, "text": "A"},
            {"start": 1.0, "end": 2.0, "text": "B"},
        ],
        language="zh",
        model="large-v3",
        device="cpu",
        compute_type="int8",
        duration_seconds=3.0,
    )
    calls: list[Path] = []
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)

    def transcribe(path: Path):
        calls.append(path)
        return transcription

    monkeypatch.setattr("app.services.video._transcribe_local_media", transcribe)
    media_path = tmp_path / "upload.mp4"

    result = process_uploaded_video(media_path, "video.mp4", tmp_path)

    assert calls == [media_path]
    assert result["type"] == "transcribed"
    assert result["selection_mode"] == "fallback"
    assert result["language"] == "zh"
    assert result["txt"] == "A,B"
    assert result["transcription_model"] == "large-v3"
    assert result["transcription_device"] == "cpu"


def test_no_text_subtitle_and_no_audio_is_controlled(monkeypatch, tmp_path) -> None:
    media = MediaInfo(2.0, (MediaStream(0, "video", "h264"),))
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)
    monkeypatch.setattr(
        "app.services.video._transcribe_local_media",
        lambda _path: pytest.fail("Whisper must not run"),
    )

    with pytest.raises(VideoExtractionError) as error:
        process_uploaded_video(tmp_path / "upload.mp4", "video.mp4", tmp_path)

    assert error.value.code == "no_transcript_source_available"


def test_ffmpeg_subtitle_failure_is_controlled(tmp_path) -> None:
    stream = MediaStream(2, "subtitle", "subrip", "en")
    with pytest.raises(VideoExtractionError) as error:
        extract_embedded_vtt(
            tmp_path / "video.mkv",
            stream,
            tmp_path / "subtitle.vtt",
            runner=lambda *_args, **_kwargs: completed({}, returncode=1),
        )
    assert error.value.code == "embedded_subtitle_extraction_failed"


def test_safe_upload_name_blocks_unsupported_and_removes_traversal() -> None:
    assert safe_upload_name("../../private/lesson.MKV") == ("lesson.MKV", ".mkv")
    assert safe_upload_name(r"..\..\private\lesson.mp4") == (
        "lesson.mp4",
        ".mp4",
    )
    with pytest.raises(VideoExtractionError) as error:
        safe_upload_name("payload.exe")
    assert error.value.code == "unsupported_video_format"


def test_store_upload_reads_in_chunks_and_enforces_limit(tmp_path) -> None:
    class ChunkedUpload:
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.read_sizes: list[int] = []

        async def read(self, size: int):
            self.read_sizes.append(size)
            return self.chunks.pop(0)

    upload = ChunkedUpload([b"abc", b"def", b""])
    destination = tmp_path / "upload.mp4"
    stored = asyncio.run(
        store_upload(
            upload,
            destination,
            VideoUploadSettings(max_bytes=10, chunk_bytes=4),
        )
    )
    assert stored == 6
    assert upload.read_sizes == [4, 4, 4]
    assert destination.read_bytes() == b"abcdef"

    oversized = ChunkedUpload([b"1234", b"5678", b""])
    with pytest.raises(VideoExtractionError) as error:
        asyncio.run(
            store_upload(
                oversized,
                tmp_path / "large.mp4",
                VideoUploadSettings(max_bytes=6, chunk_bytes=4),
            )
        )
    assert error.value.code == "upload_too_large"
