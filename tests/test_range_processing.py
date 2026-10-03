from pathlib import Path

import pytest

from app.services.errors import VideoExtractionError
from app.services.video import MediaInfo, MediaStream, process_uploaded_video
from app.services.whisper import TranscriptionResult
from app.services.youtube import get_subtitle


RANGED_VTT = (
    "WEBVTT\n\n"
    "00:00:30.000 --> 00:00:50.000\nBefore\n\n"
    "00:00:55.000 --> 00:01:10.000\nStart overlap\n\n"
    "00:01:20.123 --> 00:01:30.456\nInside\n\n"
    "00:01:55.000 --> 00:02:05.000\nEnd overlap\n\n"
    "00:02:10.000 --> 00:02:20.000\nAfter\n"
)


def youtube_info(
    *, subtitles=True, duration: float = 300.0
) -> dict[str, object]:
    return {
        "id": "abc123",
        "title": "Range example",
        "duration": duration,
        "subtitles": {"en": [{"ext": "vtt", "url": "signed"}]} if subtitles else {},
        "automatic_captions": {},
        "formats": [
            {
                "format_id": "251",
                "ext": "webm",
                "acodec": "opus",
                "vcodec": "none",
                "url": "signed-audio",
            }
        ],
    }


def transcription(*, stopped: bool = False) -> TranscriptionResult:
    return TranscriptionResult(
        segments=[
            {"start": 1.237, "end": 3.456, "text": "First"},
            {"start": 10.0, "end": 20.0, "text": "Second"},
        ],
        language="en",
        model="large-v3",
        device="cpu",
        compute_type="int8",
        duration_seconds=2.0,
        stopped=stopped,
    )


def test_youtube_subtitle_range_filters_clamps_and_never_uses_whisper(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: youtube_info())
    monkeypatch.setattr("app.services.youtube._download_selected_vtt", lambda *_args: RANGED_VTT)
    monkeypatch.setattr(
        "app.services.youtube._fallback_transcription",
        lambda *_args, **_kwargs: pytest.fail("Whisper must not run"),
    )

    result = get_subtitle(
        "https://www.youtube.com/watch?v=abc123",
        start_time="0:1",
        end_time="0:2",
    )

    assert result["range_start"] == 60.0
    assert result["range_end"] == 120.0
    assert result["segments"] == [
        {"start": 60.0, "end": 70.0, "text": "Start overlap"},
        {"start": 80.123, "end": 90.456, "text": "Inside"},
        {"start": 115.0, "end": 120.0, "text": "End overlap"},
    ]
    assert result["txt"] == "Start overlap,Inside,End overlap"
    assert "00:01:00.000 --> 00:01:10.000" in result["vtt"]
    assert "00:01:55,000 --> 00:02:00,000" in result["srt"]
    assert "Before" not in result["vtt"]
    assert "After" not in result["srt"]


def test_empty_youtube_subtitle_window_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: youtube_info())
    monkeypatch.setattr(
        "app.services.youtube._download_selected_vtt",
        lambda *_args: "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nEarly\n",
    )
    monkeypatch.setattr(
        "app.services.youtube._fallback_transcription",
        lambda *_args, **_kwargs: pytest.fail("Whisper must not run"),
    )

    result = get_subtitle(
        "https://www.youtube.com/watch?v=abc123",
        start_time="0:2",
        end_time="0:3",
    )

    assert result["segment_count"] == 0
    assert result["txt"] == ""
    assert result["vtt"] == "WEBVTT\n\n"
    assert result["srt"] == ""


def test_untouched_default_end_clamps_for_short_youtube_media(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: youtube_info(duration=120.0),
    )
    monkeypatch.setattr(
        "app.services.youtube._download_selected_vtt",
        lambda *_args: "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nShort\n",
    )

    result = get_subtitle(
        "https://www.youtube.com/watch?v=abc123",
        start_time="0:0",
        end_time="0:10",
        end_time_is_default=True,
    )

    assert result["range_start"] == 0.0
    assert result["range_end"] == 120.0
    assert result["txt"] == "Short"


def test_explicit_out_of_range_youtube_end_remains_rejected(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: youtube_info(duration=120.0),
    )

    with pytest.raises(VideoExtractionError) as error:
        get_subtitle(
            "https://www.youtube.com/watch?v=abc123",
            start_time="0:0",
            end_time="0:10",
        )

    assert error.value.code == "invalid_time_range"


def test_embedded_subtitle_range_uses_existing_track(monkeypatch, tmp_path) -> None:
    media = MediaInfo(
        300.0,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(1, "audio", "aac"),
            MediaStream(2, "subtitle", "subrip", "en", True),
        ),
    )
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)
    monkeypatch.setattr("app.services.video.extract_embedded_vtt", lambda *_args: RANGED_VTT)
    monkeypatch.setattr(
        "app.services.video._transcribe_local_media",
        lambda *_args: pytest.fail("Whisper must not run"),
    )

    result = process_uploaded_video(
        tmp_path / "upload.mkv",
        "upload.mkv",
        tmp_path,
        start_time="0:1",
        end_time="0:2",
    )

    assert result["type"] == "embedded"
    assert result["range_start"] == 60.0
    assert result["range_end"] == 120.0
    assert [segment["text"] for segment in result["segments"]] == [
        "Start overlap",
        "Inside",
        "End overlap",
    ]


def test_untouched_default_end_clamps_for_short_upload(
    monkeypatch, tmp_path
) -> None:
    media = MediaInfo(
        120.0,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(1, "audio", "aac"),
            MediaStream(2, "subtitle", "subrip", "en", True),
        ),
    )
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)
    monkeypatch.setattr(
        "app.services.video.extract_embedded_vtt",
        lambda *_args: "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nShort\n",
    )

    result = process_uploaded_video(
        tmp_path / "upload.mp4",
        "upload.mp4",
        tmp_path,
        start_time="0:0",
        end_time="0:10",
        end_time_is_default=True,
    )

    assert result["range_start"] == 0.0
    assert result["range_end"] == 120.0
    assert result["txt"] == "Short"


def test_untouched_default_end_remains_ten_minutes_for_long_upload(
    monkeypatch, tmp_path
) -> None:
    media = MediaInfo(
        1200.0,
        (
            MediaStream(0, "video", "h264"),
            MediaStream(1, "subtitle", "subrip", "en", True),
        ),
    )
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)
    monkeypatch.setattr(
        "app.services.video.extract_embedded_vtt",
        lambda *_args: "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nLong\n",
    )

    result = process_uploaded_video(
        tmp_path / "upload.mp4",
        "upload.mp4",
        tmp_path,
        start_time="0:0",
        end_time="0:10",
        end_time_is_default=True,
    )

    assert result["range_start"] == 0.0
    assert result["range_end"] == 600.0


def test_explicit_out_of_range_upload_end_remains_rejected(
    monkeypatch, tmp_path
) -> None:
    media = MediaInfo(
        120.0,
        (MediaStream(0, "video", "h264"), MediaStream(1, "audio", "aac")),
    )
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)

    with pytest.raises(VideoExtractionError) as error:
        process_uploaded_video(
            tmp_path / "upload.mp4",
            "upload.mp4",
            tmp_path,
            start_time="0:0",
            end_time="0:10",
        )

    assert error.value.code == "invalid_time_range"


def test_upload_whisper_range_clips_offsets_and_cleans(monkeypatch, tmp_path) -> None:
    media = MediaInfo(
        300.0,
        (MediaStream(0, "video", "h264"), MediaStream(1, "audio", "aac")),
    )
    clip_directory: Path | None = None
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)

    def clip(_source, output, start, end):
        nonlocal clip_directory
        clip_directory = output.parent
        assert (start, end) == (60.0, 120.0)
        output.write_bytes(b"range audio")
        return output

    monkeypatch.setattr("app.services.video.create_range_audio_clip", clip)
    monkeypatch.setattr("app.services.video._transcribe_local_media", lambda path: transcription())

    result = process_uploaded_video(
        tmp_path / "upload.mp4",
        "upload.mp4",
        tmp_path,
        start_time="0:1",
        end_time="0:2",
    )

    assert result["language"] == "en"
    assert result["segments"][0] == {
        "start": 61.237,
        "end": 63.456,
        "text": "First",
    }
    assert result["segments"][1]["start"] == 70.0
    assert result["txt"] == "First,Second"
    assert "00:01:01.237 --> 00:01:03.456" in result["vtt"]
    assert "00:01:10,000 --> 00:01:20,000" in result["srt"]
    assert clip_directory is not None and not clip_directory.exists()


def test_full_upload_does_not_create_range_clip(monkeypatch, tmp_path) -> None:
    media = MediaInfo(
        300.0,
        (MediaStream(0, "video", "h264"), MediaStream(1, "audio", "aac")),
    )
    source = tmp_path / "upload.mp4"
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)
    monkeypatch.setattr(
        "app.services.video.create_range_audio_clip",
        lambda *_args: pytest.fail("Full media must not create a range clip"),
    )
    monkeypatch.setattr("app.services.video._transcribe_local_media", lambda path: transcription())

    result = process_uploaded_video(source, "upload.mp4", tmp_path)

    assert result["range_start"] == 0.0
    assert result["range_end"] == 300.0
    assert result["segments"][0]["start"] == 1.237


def test_upload_range_clip_is_cleaned_after_failure(monkeypatch, tmp_path) -> None:
    media = MediaInfo(
        300.0,
        (MediaStream(0, "video", "h264"), MediaStream(1, "audio", "aac")),
    )
    clip_directory: Path | None = None
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)

    def clip(_source, output, _start, _end):
        nonlocal clip_directory
        clip_directory = output.parent
        output.write_bytes(b"range audio")
        return output

    def fail(_path):
        raise VideoExtractionError(502, "whisper_transcription_failed", "failed")

    monkeypatch.setattr("app.services.video.create_range_audio_clip", clip)
    monkeypatch.setattr("app.services.video._transcribe_local_media", fail)

    with pytest.raises(VideoExtractionError):
        process_uploaded_video(
            tmp_path / "upload.mp4",
            "upload.mp4",
            tmp_path,
            start_time="0:1",
            end_time="0:2",
        )

    assert clip_directory is not None and not clip_directory.exists()


def test_youtube_whisper_range_offsets_and_cleans(monkeypatch) -> None:
    temporary_path: Path | None = None
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: youtube_info(subtitles=False),
    )

    def download(_url, directory):
        path = directory / "audio.webm"
        path.write_bytes(b"audio")
        return path

    def clip(_source, output, _start, _end):
        nonlocal temporary_path
        temporary_path = output
        output.write_bytes(b"clip")
        return output

    monkeypatch.setattr("app.services.youtube._download_audio_only", download)
    monkeypatch.setattr("app.services.youtube.create_range_audio_clip", clip)
    monkeypatch.setattr("app.services.youtube._transcribe_audio", lambda _path: transcription())

    result = get_subtitle(
        "https://www.youtube.com/watch?v=abc123",
        start_time="0:1",
        end_time="0:2",
    )

    assert result["type"] == "transcribed"
    assert result["segments"][0]["start"] == 61.237
    assert result["range_start"] == 60.0
    assert result["range_end"] == 120.0
    assert temporary_path is not None and not temporary_path.exists()


def test_ranged_stop_callback_and_result_use_original_timeline(
    monkeypatch, tmp_path
) -> None:
    media = MediaInfo(
        300.0,
        (MediaStream(0, "video", "h264"), MediaStream(1, "audio", "aac")),
    )
    observed: list[dict[str, object]] = []
    monkeypatch.setattr("app.services.video.probe_media", lambda _path: media)

    def clip(_source, output, _start, _end):
        output.write_bytes(b"clip")
        return output

    def transcribe(_path, on_segment, _should_stop):
        on_segment({"start": 1.25, "end": 2.75, "text": "Partial"})
        return TranscriptionResult(
            segments=[{"start": 1.25, "end": 2.75, "text": "Partial"}],
            language="en",
            model="large-v3",
            device="cpu",
            compute_type="int8",
            duration_seconds=1.0,
            stopped=True,
        )

    monkeypatch.setattr("app.services.video.create_range_audio_clip", clip)
    monkeypatch.setattr("app.services.video._transcribe_local_media", transcribe)

    result = process_uploaded_video(
        tmp_path / "upload.mp4",
        "upload.mp4",
        tmp_path,
        start_time="0:1",
        end_time="0:2",
        on_segment=observed.append,
        should_stop=lambda: False,
    )

    assert result["_stopped"] is True
    assert observed == [{"start": 61.25, "end": 62.75, "text": "Partial"}]
    assert result["segments"] == observed
    assert "00:01:01.250" in result["vtt"]
    assert "00:01:01,250" in result["srt"]
