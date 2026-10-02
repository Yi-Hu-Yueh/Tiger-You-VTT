import pytest

from app.services.transcript import (
    normalize_transcribed_segments,
    normalize_segments,
    parse_vtt,
    transcript_to_srt,
    transcript_to_txt,
    transcript_to_vtt,
)
from app.services.youtube import VideoExtractionError


def vtt_with(body: str) -> str:
    return f"WEBVTT\n\n{body}\n"


def test_parses_standard_vtt_cue_with_milliseconds() -> None:
    segments = parse_vtt(vtt_with("00:00:00.520 --> 00:00:04.860\nHello world."))

    assert segments == [{"start": 0.52, "end": 4.86, "text": "Hello world."}]


def test_parses_multiline_cue_as_readable_text() -> None:
    segments = parse_vtt(vtt_with("00:00:01.000 --> 00:00:03.000\nFirst line\nsecond line"))

    assert segments[0]["text"] == "First line second line"


def test_parses_cue_settings() -> None:
    segments = parse_vtt(
        vtt_with("00:00:01.000 --> 00:00:03.000 align:start position:0%\nPositioned")
    )

    assert segments[0] == {"start": 1.0, "end": 3.0, "text": "Positioned"}


def test_preserves_unicode_text() -> None:
    segments = parse_vtt(vtt_with("00:00:01.000 --> 00:00:02.000\n你好，世界 🌏"))

    assert segments[0]["text"] == "你好，世界 🌏"


def test_decodes_html_entities() -> None:
    segments = parse_vtt(vtt_with("00:00:01.000 --> 00:00:02.000\nRock &amp; roll &lt;3"))

    assert segments[0]["text"] == "Rock & roll <3"


def test_removes_caption_and_embedded_timestamp_tags() -> None:
    segments = parse_vtt(
        vtt_with(
            "00:00:01.000 --> 00:00:04.000\n"
            "<c>Today we're</c><00:00:02.500><c.colorE5E5E5> talking.</c>"
        )
    )

    assert segments[0]["text"] == "Today we're talking."


def test_removes_exact_consecutive_duplicate_cue() -> None:
    segments = normalize_segments(
        [
            {"start": 0.0, "end": 2.0, "text": "Same caption"},
            {"start": 1.5, "end": 3.0, "text": "Same caption"},
        ]
    )

    assert segments == [{"start": 0.0, "end": 2.0, "text": "Same caption"}]


def test_reduces_suffix_prefix_rolling_overlap() -> None:
    segments = normalize_segments(
        [
            {"start": 0.0, "end": 2.0, "text": "Today we're talking"},
            {"start": 1.5, "end": 3.5, "text": "Today we're talking about agents"},
            {"start": 3.0, "end": 5.0, "text": "about agents and tools"},
        ]
    )

    assert [segment["text"] for segment in segments] == [
        "Today we're talking",
        "about agents",
        "and tools",
    ]


def test_reduces_repeated_single_word_rolling_fragment() -> None:
    segments = normalize_segments(
        [
            {"start": 0.0, "end": 1.5, "text": "we are"},
            {"start": 1.5, "end": 2.0, "text": "we are thinking"},
            {"start": 2.0, "end": 2.01, "text": "thinking"},
            {"start": 2.01, "end": 4.0, "text": "thinking of a plan"},
        ]
    )

    assert [segment["text"] for segment in segments] == [
        "we are",
        "thinking",
        "of a plan",
    ]


def test_preserves_legitimate_repetition_after_a_timing_gap() -> None:
    segments = normalize_segments(
        [
            {"start": 0.0, "end": 1.0, "text": "Again and again"},
            {"start": 5.0, "end": 6.0, "text": "Again and again"},
        ]
    )

    assert [segment["text"] for segment in segments] == [
        "Again and again",
        "Again and again",
    ]


def test_removes_empty_cue() -> None:
    segments = normalize_segments(
        [
            {"start": 0.0, "end": 1.0, "text": "  \n "},
            {"start": 1.0, "end": 2.0, "text": "Kept"},
        ]
    )

    assert segments == [{"start": 1.0, "end": 2.0, "text": "Kept"}]


def test_outputs_segments_in_chronological_order() -> None:
    segments = normalize_segments(
        [
            {"start": 5.0, "end": 6.0, "text": "Second"},
            {"start": 1.0, "end": 2.0, "text": "First"},
        ]
    )

    assert [segment["start"] for segment in segments] == [1.0, 5.0]


def test_generates_txt_with_ascii_comma_separator() -> None:
    txt = transcript_to_txt(
        [
            {"start": 0.0, "end": 1.0, "text": "First line"},
            {"start": 1.0, "end": 2.0, "text": "Second line"},
        ]
    )

    assert txt == "First line,Second line"
    assert "\n" not in txt
    assert not txt.startswith(",")
    assert not txt.endswith(",")


def test_txt_preserves_unicode_and_punctuation_while_skipping_empty_items() -> None:
    txt = transcript_to_txt(
        [
            {"start": 0.0, "end": 1.0, "text": "你以为RG只是查资料加生成"},
            {"start": 1.0, "end": 1.5, "text": "  "},
            {"start": 1.5, "end": 2.0, "text": "其实90%的效果差距，真的。"},
            {"start": 2.0, "end": 3.0, "text": "藏在那几行检索代码里面"},
        ]
    )

    assert txt == (
        "你以为RG只是查资料加生成,"
        "其实90%的效果差距，真的。,"
        "藏在那几行检索代码里面"
    )
    assert ",," not in txt
    assert "\n" not in txt


def test_generates_vtt_with_header_and_millisecond_timestamps() -> None:
    vtt = transcript_to_vtt(
        [{"start": 0.52, "end": 3661.007, "text": "你好"}]
    )

    assert vtt == "WEBVTT\n\n00:00:00.520 --> 01:01:01.007\n你好\n"


def test_whisper_normalization_does_not_remove_repeated_speech() -> None:
    segments = normalize_transcribed_segments(
        [
            {"start": 0.0, "end": 1.0, "text": " again "},
            {"start": 1.0, "end": 2.0, "text": "again"},
            {"start": 2.0, "end": 2.5, "text": "  "},
        ]
    )

    assert segments == [
        {"start": 0.0, "end": 1.0, "text": "again"},
        {"start": 1.0, "end": 2.0, "text": "again"},
    ]


def test_whisper_timestamp_and_serializers_do_not_stretch_segment() -> None:
    segments = normalize_transcribed_segments(
        [{"start": 71.14, "end": 72.70, "text": "Timed speech"}]
    )

    assert segments == [
        {"start": 71.14, "end": 72.7, "text": "Timed speech"}
    ]
    assert transcript_to_vtt(segments) == (
        "WEBVTT\n\n00:01:11.140 --> 00:01:12.700\nTimed speech\n"
    )
    assert transcript_to_srt(segments) == (
        "1\n00:01:11,140 --> 00:01:12,700\nTimed speech\n"
    )


def test_generates_numbered_srt_with_millisecond_timestamps() -> None:
    srt = transcript_to_srt(
        [
            {"start": 0.52, "end": 4.86, "text": "First"},
            {"start": 3661.007, "end": 3662.1, "text": "Second"},
        ]
    )

    assert srt == (
        "1\n00:00:00,520 --> 00:00:04,860\nFirst\n\n"
        "2\n01:01:01,007 --> 01:01:02,100\nSecond\n"
    )


def test_rejects_malformed_vtt() -> None:
    with pytest.raises(VideoExtractionError) as error:
        parse_vtt("not a VTT file")

    assert error.value.code == "malformed_vtt"


def test_rejects_empty_subtitle() -> None:
    with pytest.raises(VideoExtractionError) as error:
        parse_vtt(" \n\t")

    assert error.value.code == "empty_subtitle"

