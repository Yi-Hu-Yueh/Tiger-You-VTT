import pytest

from app.services.youtube import (
    VideoExtractionError,
    get_subtitle,
    select_subtitle_track,
)


def vtt_track(name: str | None = None, *, usable: bool = True) -> list[dict[str, object]]:
    track: dict[str, object] = {
        "ext": "vtt" if usable else "srv3",
        "url": "https://signed.test/subtitle",
    }
    if name is not None:
        track["name"] = name
    return [track]


def info(
    *,
    subtitles: dict[str, object] | None = None,
    automatic: dict[str, object] | None = None,
    language: str | None = None,
) -> dict[str, object]:
    return {
        "id": "abc123",
        "title": "Example video",
        "duration": 10,
        "language": language,
        "subtitles": subtitles or {},
        "automatic_captions": automatic or {},
    }


@pytest.mark.parametrize(
    ("tracks", "expected"),
    [
        ({"en": vtt_track(), "zh-TW": vtt_track()}, "zh-TW"),
        ({"en": vtt_track(), "zh-Hant": vtt_track()}, "zh-Hant"),
        ({"en": vtt_track(), "zh": vtt_track()}, "zh"),
        ({"fr": vtt_track(), "en": vtt_track()}, "en"),
    ],
)
def test_preferred_manual_track_priority(
    tracks: dict[str, object], expected: str
) -> None:
    selected = select_subtitle_track(info(subtitles=tracks))

    assert (selected.language, selected.type, selected.selection_mode) == (
        expected,
        "manual",
        "auto",
    )


def test_other_manual_track_beats_translated_automatic_caption() -> None:
    selected = select_subtitle_track(
        info(
            subtitles={"ja": vtt_track()},
            automatic={"en": vtt_track("English")},
        )
    )

    assert (selected.language, selected.type) == ("ja", "manual")


def test_video_language_breaks_other_manual_track_tie() -> None:
    selected = select_subtitle_track(
        info(
            subtitles={"de": vtt_track(), "ja": vtt_track()},
            language="ja",
        )
    )

    assert selected.language == "ja"


def test_other_manual_fallback_is_deterministic() -> None:
    selected = select_subtitle_track(
        info(subtitles={"ja": vtt_track(), "de": vtt_track()})
    )

    assert selected.language == "de"


def test_en_orig_is_selected_when_no_manual_track_exists() -> None:
    selected = select_subtitle_track(
        info(
            automatic={
                "fr": vtt_track("French"),
                "en-orig": vtt_track("English (Original)"),
            }
        )
    )

    assert (selected.language, selected.type) == ("en-orig", "auto")


def test_original_chinese_auto_precedes_en_orig() -> None:
    selected = select_subtitle_track(
        info(
            automatic={
                "en-orig": vtt_track("English (Original)"),
                "zh-TW-orig": vtt_track("Chinese (Taiwan) (Original)"),
            }
        )
    )

    assert selected.language == "zh-TW-orig"


def test_metadata_original_marker_preserves_nonstandard_exact_id() -> None:
    selected = select_subtitle_track(
        info(
            automatic={
                "en": vtt_track("English (Original)"),
                "fr": vtt_track("French"),
            }
        )
    )

    assert selected.language == "en"


def test_translated_auto_is_not_preferred_over_known_original() -> None:
    selected = select_subtitle_track(
        info(
            automatic={
                "aa": vtt_track("Afar"),
                "en-orig": vtt_track("English (Original)"),
            }
        )
    )

    assert selected.language == "en-orig"


def test_unusable_high_priority_track_falls_through() -> None:
    selected = select_subtitle_track(
        info(
            subtitles={
                "zh-TW": vtt_track(usable=False),
                "en": vtt_track(),
            }
        )
    )

    assert selected.language == "en"


def test_no_usable_tracks_returns_controlled_error() -> None:
    with pytest.raises(VideoExtractionError) as error:
        select_subtitle_track(
            info(
                subtitles={"en": vtt_track(usable=False)},
                automatic={"en-orig": vtt_track(usable=False)},
            )
        )

    assert error.value.code == "no_subtitles_available"


def test_explicit_selection_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: info(subtitles={"en": vtt_track()}),
    )

    with pytest.raises(VideoExtractionError) as error:
        get_subtitle("https://www.youtube.com/watch?v=abc123", "fr", "manual")

    assert error.value.code == "subtitle_track_not_found"


def test_get_subtitle_auto_mode_reports_selected_track(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: info(subtitles={"en": vtt_track()}),
    )

    def fake_download(_url, language, track_type, _directory):
        assert (language, track_type) == ("en", "manual")
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n"

    monkeypatch.setattr("app.services.youtube._download_selected_vtt", fake_download)

    result = get_subtitle("https://www.youtube.com/watch?v=abc123")

    assert result["language"] == "en"
    assert result["type"] == "manual"
    assert result["selection_mode"] == "auto"


def test_get_subtitle_explicit_manual_mode_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: info(subtitles={"en": vtt_track()}),
    )

    def fake_download(_url, language, track_type, _directory):
        assert (language, track_type) == ("en", "manual")
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n"

    monkeypatch.setattr("app.services.youtube._download_selected_vtt", fake_download)

    result = get_subtitle(
        "https://www.youtube.com/watch?v=abc123", "en", "manual"
    )

    assert result["selection_mode"] == "explicit"
