from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from app.services.youtube import (
    VideoExtractionError,
    _download_selected_vtt,
    _validate_selected_track,
    get_subtitle,
)


SAMPLE_VTT = (
    "WEBVTT\n\n"
    "00:00:00.500 --> 00:00:02.750\nHello &amp; welcome.\n\n"
    "00:00:02.750 --> 00:00:04.000\nNext segment.\n"
)


def raw_info(**overrides: object) -> dict[str, object]:
    info: dict[str, object] = {
        "id": "abc123",
        "title": "Example video",
        "duration": 42.5,
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "subtitles": {
            "en": [{"ext": "vtt", "url": "https://signed.test/manual"}]
        },
        "automatic_captions": {
            "en-orig": [{"ext": "vtt", "url": "https://signed.test/auto"}]
        },
    }
    info.update(overrides)
    return info


def test_selected_manual_subtitle_exists() -> None:
    _validate_selected_track(raw_info(), "en", "manual")


def test_selected_automatic_caption_exists_with_exact_code() -> None:
    _validate_selected_track(raw_info(), "en-orig", "auto")


def test_manual_request_cannot_use_auto_only_track() -> None:
    with pytest.raises(VideoExtractionError) as error:
        _validate_selected_track(raw_info(), "en-orig", "manual")

    assert error.value.code == "subtitle_track_not_found"


def test_auto_request_cannot_use_manual_only_track() -> None:
    with pytest.raises(VideoExtractionError) as error:
        _validate_selected_track(raw_info(), "en", "auto")

    assert error.value.code == "subtitle_track_not_found"


def test_nonexistent_language_is_rejected() -> None:
    with pytest.raises(VideoExtractionError) as error:
        _validate_selected_track(raw_info(), "fr", "manual")

    assert error.value.code == "subtitle_track_not_found"


def test_track_without_vtt_is_rejected() -> None:
    info = raw_info(subtitles={"en": [{"ext": "srv3", "url": "signed"}]})

    with pytest.raises(VideoExtractionError) as error:
        _validate_selected_track(info, "en", "manual")

    assert error.value.code == "vtt_unavailable"


@pytest.mark.parametrize(
    ("track_type", "language", "writesubtitles", "writeautomaticsub"),
    [
        ("manual", "en", True, False),
        ("auto", "en-orig", False, True),
    ],
)
def test_download_uses_only_selected_exact_track(
    tmp_path: Path,
    track_type: str,
    language: str,
    writesubtitles: bool,
    writeautomaticsub: bool,
) -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl

    def write_vtt(_url: str, download: bool) -> None:
        assert download is True
        (tmp_path / f"subtitle.{language}.vtt").write_text(SAMPLE_VTT, encoding="utf-8")

    ydl.extract_info.side_effect = write_vtt

    with patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl) as factory:
        result = _download_selected_vtt(tmp_path.as_uri(), language, track_type, tmp_path)

    options = factory.call_args.args[0]
    assert options["skip_download"] is True
    assert options["writesubtitles"] is writesubtitles
    assert options["writeautomaticsub"] is writeautomaticsub
    assert options["subtitleslangs"] == [language]
    assert options["subtitlesformat"] == "vtt"
    assert result == SAMPLE_VTT


def test_subtitle_download_failure_is_controlled(tmp_path: Path) -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = DownloadError("private HTTP diagnostic")

    with patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl):
        with pytest.raises(VideoExtractionError) as error:
            _download_selected_vtt(
                "https://www.youtube.com/watch?v=abc123", "en", "manual", tmp_path
            )

    assert error.value.code == "subtitle_download_failed"
    assert "private HTTP diagnostic" not in error.value.message


def test_get_subtitle_preserves_vtt_and_cleans_temporary_directory(
    monkeypatch,
) -> None:
    temporary_path: Path | None = None

    def fake_download(
        _url: str, language: str, track_type: str, directory: Path
    ) -> str:
        nonlocal temporary_path
        temporary_path = directory
        assert language == "en-orig"
        assert track_type == "auto"
        (directory / "marker").write_text("temporary", encoding="utf-8")
        return SAMPLE_VTT

    monkeypatch.setattr("app.services.youtube._extract_raw_info", lambda _url: raw_info())
    monkeypatch.setattr("app.services.youtube._download_selected_vtt", fake_download)

    result = get_subtitle(
        "https://www.youtube.com/watch?v=abc123", "en-orig", "auto"
    )

    assert result["language"] == "en-orig"
    assert result["type"] == "auto"
    assert result["selection_mode"] == "explicit"
    assert result["vtt"] == SAMPLE_VTT
    assert result["segments"] == [
        {"start": 0.5, "end": 2.75, "text": "Hello & welcome."},
        {"start": 2.75, "end": 4.0, "text": "Next segment."},
    ]
    assert result["txt"] == "Hello & welcome.,Next segment."
    assert "\n" not in result["txt"]
    assert result["srt"].startswith("1\n00:00:00,500 --> 00:00:02,750")
    assert "\n\n2\n00:00:02,750 --> 00:00:04,000\nNext segment." in result["srt"]
    assert temporary_path is not None
    assert not temporary_path.exists()

