from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from app.services.whisper import TranscriptionResult
from app.services.youtube import (
    VideoExtractionError,
    _download_audio_only,
    get_subtitle,
)


def vtt_track() -> list[dict[str, str]]:
    return [{"ext": "vtt", "url": "https://signed.test/subtitle"}]


def audio_format() -> dict[str, str]:
    return {
        "format_id": "251",
        "ext": "webm",
        "acodec": "opus",
        "vcodec": "none",
        "url": "https://signed.test/audio",
    }


def info(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "abc123",
        "title": "Example video",
        "duration": 12.5,
        "subtitles": {},
        "automatic_captions": {},
        "formats": [audio_format()],
    }
    value.update(overrides)
    return value


def transcription() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[
            {"start": 0.25, "end": 2.5, "text": "你好，課程。"},
            {"start": 2.5, "end": 4.0, "text": "下一段"},
        ],
        language="zh",
        model="large-v3",
        device="cpu",
        compute_type="int8",
        duration_seconds=3.25,
    )


@pytest.mark.parametrize(
    ("tracks_key", "language", "track_type"),
    [
        ("subtitles", "en", "manual"),
        ("automatic_captions", "en-orig", "auto"),
    ],
)
def test_existing_tracks_never_invoke_whisper(
    monkeypatch, tracks_key: str, language: str, track_type: str
) -> None:
    metadata = info(**{tracks_key: {language: vtt_track()}})
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: metadata)
    monkeypatch.setattr(
        "app.services.youtube._download_selected_vtt",
        lambda *_args: "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n",
    )
    monkeypatch.setattr(
        "app.services.youtube._fallback_transcription",
        lambda *_args: pytest.fail("Whisper fallback must not run"),
    )

    result = get_subtitle("https://www.youtube.com/watch?v=abc123")

    assert result["type"] == track_type
    assert result["selection_mode"] == "auto"


@pytest.mark.parametrize("track_type", ["manual", "auto"])
def test_explicit_missing_track_never_falls_back(monkeypatch, track_type: str) -> None:
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: info())
    monkeypatch.setattr(
        "app.services.youtube._fallback_transcription",
        lambda *_args: pytest.fail("Explicit selection must not fall back"),
    )

    with pytest.raises(VideoExtractionError) as error:
        get_subtitle(
            "https://www.youtube.com/watch?v=abc123", "missing", track_type
        )

    assert error.value.code == "subtitle_track_not_found"


def test_no_subtitles_uses_fallback_and_reports_transcribed_metadata(
    monkeypatch,
) -> None:
    temporary_path: Path | None = None

    def fake_download(_url: str, directory: Path) -> Path:
        nonlocal temporary_path
        temporary_path = directory
        audio = directory / "audio.webm"
        audio.write_bytes(b"audio")
        return audio

    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: info())
    monkeypatch.setattr("app.services.youtube._download_audio_only", fake_download)
    monkeypatch.setattr(
        "app.services.youtube._transcribe_audio", lambda _path: transcription()
    )

    result = get_subtitle("https://www.youtube.com/watch?v=abc123")

    assert result["type"] == "transcribed"
    assert result["selection_mode"] == "fallback"
    assert result["language"] == "zh"
    assert result["transcription_model"] == "large-v3"
    assert result["transcription_device"] == "cpu"
    assert result["transcription_compute_type"] == "int8"
    assert result["segment_count"] == 2
    assert result["segments"] == [
        {"start": 0.25, "end": 2.5, "text": "你好，課程。"},
        {"start": 2.5, "end": 4.0, "text": "下一段"},
    ]
    assert result["vtt"].startswith("WEBVTT\n\n00:00:00.250")
    assert "00:00:02.500 --> 00:00:04.000\n下一段" in result["vtt"]
    assert result["txt"] == "你好，課程。,下一段"
    assert "\n" not in result["txt"]
    assert result["srt"].startswith("1\n00:00:00,250 --> 00:00:02,500")
    assert "\n\n2\n00:00:02,500 --> 00:00:04,000\n下一段" in result["srt"]
    assert temporary_path is not None and not temporary_path.exists()


def test_audio_download_uses_audio_only_configuration(tmp_path: Path) -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl

    def write_audio(_url: str, download: bool) -> None:
        assert download is True
        (tmp_path / "audio.webm").write_bytes(b"audio-only")

    ydl.extract_info.side_effect = write_audio
    with (
        patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl) as factory,
        patch(
            "app.services.youtube.probe_media_info",
            return_value=SimpleNamespace(has_audio=True, duration=1.0),
        ),
    ):
        result = _download_audio_only("https://youtube.test/watch?v=x", tmp_path)

    options = factory.call_args.args[0]
    assert options["skip_download"] is False
    assert options["format"] == "bestaudio"
    assert result.name == "audio.webm"
    assert not list(tmp_path.glob("*.mp4"))


def test_audio_download_failure_is_controlled(tmp_path: Path) -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = DownloadError("signed URL expired")

    with patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl):
        with pytest.raises(VideoExtractionError) as error:
            _download_audio_only("https://youtube.test/watch?v=x", tmp_path)

    assert error.value.code == "audio_download_failed"
    assert "signed URL" not in error.value.message


def test_transient_audio_403_is_retried_once_then_validated(tmp_path: Path) -> None:
    first = MagicMock()
    first.__enter__.return_value = first
    first.extract_info.side_effect = DownloadError("HTTP Error 403: Forbidden")
    second = MagicMock()
    second.__enter__.return_value = second

    def write_audio(_url: str, download: bool) -> None:
        assert download is True
        (tmp_path / "audio.webm").write_bytes(b"audio")

    second.extract_info.side_effect = write_audio
    with (
        patch(
            "app.services.youtube.yt_dlp.YoutubeDL",
            side_effect=[first, second],
        ) as factory,
        patch(
            "app.services.youtube.probe_media_info",
            return_value=SimpleNamespace(has_audio=True, duration=2.0),
        ),
    ):
        result = _download_audio_only("https://youtube.test/watch?v=x", tmp_path)

    assert factory.call_count == 2
    assert result.name == "audio.webm"


def test_invalid_downloaded_audio_is_rejected_before_whisper(tmp_path: Path) -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = lambda *_args, **_kwargs: (
        tmp_path / "audio.webm"
    ).write_bytes(b"not-empty")

    with (
        patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl),
        patch(
            "app.services.youtube.probe_media_info",
            return_value=SimpleNamespace(has_audio=False, duration=1.0),
        ),
        pytest.raises(VideoExtractionError) as error,
    ):
        _download_audio_only("https://youtube.test/watch?v=x", tmp_path)

    assert error.value.code == "invalid_downloaded_audio"


def test_auto_subtitle_download_failure_uses_audio_fallback(monkeypatch) -> None:
    metadata = info(subtitles={"en": vtt_track()})
    expected = {"type": "transcribed", "txt": "fallback"}
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: metadata)
    monkeypatch.setattr(
        "app.services.youtube._download_selected_vtt",
        lambda *_args: (_ for _ in ()).throw(
            VideoExtractionError(502, "subtitle_download_failed", "failed")
        ),
    )
    monkeypatch.setattr(
        "app.services.youtube._fallback_transcription",
        lambda *_args: expected,
    )

    assert get_subtitle("https://www.youtube.com/watch?v=abc123") is expected


def test_explicit_subtitle_download_failure_does_not_use_whisper(monkeypatch) -> None:
    metadata = info(subtitles={"en": vtt_track()})
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: metadata)
    monkeypatch.setattr(
        "app.services.youtube._download_selected_vtt",
        lambda *_args: (_ for _ in ()).throw(
            VideoExtractionError(502, "subtitle_download_failed", "failed")
        ),
    )
    monkeypatch.setattr(
        "app.services.youtube._fallback_transcription",
        lambda *_args: pytest.fail("Explicit selection must not fall back"),
    )

    with pytest.raises(VideoExtractionError) as error:
        get_subtitle(
            "https://www.youtube.com/watch?v=abc123", "en", "manual"
        )
    assert error.value.code == "subtitle_download_failed"


def test_missing_audio_file_after_download_is_controlled(tmp_path: Path) -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl

    with patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl):
        with pytest.raises(VideoExtractionError) as error:
            _download_audio_only("https://youtube.test/watch?v=x", tmp_path)

    assert error.value.code == "audio_download_failed"


def test_no_usable_audio_is_distinct_and_whisper_is_not_called(monkeypatch) -> None:
    metadata = info(formats=[{"acodec": "none", "vcodec": "avc1", "url": "x"}])
    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: metadata)
    monkeypatch.setattr(
        "app.services.youtube._transcribe_audio",
        lambda _path: pytest.fail("Whisper must not run without audio"),
    )

    with pytest.raises(VideoExtractionError) as error:
        get_subtitle("https://www.youtube.com/watch?v=abc123")

    assert error.value.code == "no_audio_available"


def test_temporary_audio_is_cleaned_after_transcription_failure(monkeypatch) -> None:
    temporary_path: Path | None = None

    def fake_download(_url: str, directory: Path) -> Path:
        nonlocal temporary_path
        temporary_path = directory
        audio = directory / "audio.webm"
        audio.write_bytes(b"audio")
        return audio

    def fail(_path: Path):
        raise VideoExtractionError(502, "whisper_transcription_failed", "failed")

    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: info())
    monkeypatch.setattr("app.services.youtube._download_audio_only", fake_download)
    monkeypatch.setattr("app.services.youtube._transcribe_audio", fail)

    with pytest.raises(VideoExtractionError):
        get_subtitle("https://www.youtube.com/watch?v=abc123")

    assert temporary_path is not None and not temporary_path.exists()
