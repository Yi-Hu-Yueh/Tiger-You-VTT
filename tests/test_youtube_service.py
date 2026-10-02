from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from app.services.youtube import (
    VideoExtractionError,
    get_video_info,
    normalize_video_info,
)


def base_info(**overrides: object) -> dict[str, object]:
    info: dict[str, object] = {
        "id": "abc123",
        "title": "Example video",
        "channel": "Example channel",
        "uploader": "Example uploader",
        "duration": 1234.9,
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "original_url": "https://youtu.be/abc123",
        "thumbnail": "https://example.test/thumb.jpg",
        "language": "en",
    }
    info.update(overrides)
    return info


def test_normalizes_manual_subtitles_without_signed_urls() -> None:
    result = normalize_video_info(
        base_info(
            subtitles={
                "en": [
                    {"ext": "vtt", "url": "https://signed.test/vtt", "name": "English"},
                    {"ext": "srv3", "url": "https://signed.test/srv3"},
                    {"ext": "vtt", "url": "https://duplicate.test/vtt"},
                ]
            }
        )
    )

    assert result["subtitles"] == [
        {
            "language": "en",
            "type": "manual",
            "name": "English",
            "formats": [
                {"ext": "vtt", "url_available": True},
                {"ext": "srv3", "url_available": True},
            ],
        }
    ]
    assert "signed.test" not in repr(result)


def test_normalizes_automatic_captions() -> None:
    result = normalize_video_info(
        base_info(
            automatic_captions={
                "fr": [{"ext": "vtt", "url": "https://signed.test/fr"}]
            }
        )
    )

    assert result["automatic_captions"] == [
        {
            "language": "fr",
            "type": "auto",
            "name": "fr",
            "formats": [{"ext": "vtt", "url_available": True}],
        }
    ]


def test_keeps_manual_and_automatic_tracks_separate() -> None:
    result = normalize_video_info(
        base_info(
            subtitles={"en": [{"ext": "vtt", "url": "manual"}]},
            automatic_captions={"en": [{"ext": "vtt", "url": "auto"}]},
        )
    )

    assert result["subtitles"][0]["type"] == "manual"
    assert result["automatic_captions"][0]["type"] == "auto"


def test_video_without_subtitles_has_empty_track_arrays() -> None:
    result = normalize_video_info(base_info(subtitles=None, automatic_captions={}))

    assert result["subtitles"] == []
    assert result["automatic_captions"] == []


def test_get_video_info_never_requests_a_download() -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = base_info()

    with patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl) as factory:
        get_video_info("https://www.youtube.com/watch?v=abc123")

    assert factory.call_args.args[0]["skip_download"] is True
    ydl.extract_info.assert_called_once_with(
        "https://www.youtube.com/watch?v=abc123", download=False
    )


def test_extraction_failure_is_converted_to_controlled_error() -> None:
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = DownloadError("private diagnostic")

    with patch("app.services.youtube.yt_dlp.YoutubeDL", return_value=ydl):
        with pytest.raises(VideoExtractionError) as error:
            get_video_info("https://www.youtube.com/watch?v=abc123")

    assert error.value.status_code == 502
    assert error.value.code == "youtube_extraction_failed"
    assert "private diagnostic" not in error.value.message

