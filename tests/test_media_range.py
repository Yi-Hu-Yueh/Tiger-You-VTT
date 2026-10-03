from pathlib import Path
import subprocess

import pytest

from app.services.errors import VideoExtractionError
from app.services.media_range import (
    create_range_audio_clip,
    effective_default_end_time,
    filter_segments_to_range,
    offset_clip_segments,
    parse_media_time,
    resolve_media_range,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("0:3", 180.0),
        ("0:0", 0.0),
        ("0:10", 600.0),
        ("0:03", 180.0),
        ("2:25", 8700.0),
        ("10:05", 36300.0),
        ("65:08", 234480.0),
        ("100:30", 361800.0),
        (" 2:25 ", 8700.0),
    ],
)
def test_parse_media_time(value, expected) -> None:
    assert parse_media_time(value) == expected


@pytest.mark.parametrize(
    "value",
    ["2:60", "-1:20", "abc", "1", "1:", ":20", "1:2:3", "1.5:20", "00:03:30"],
)
def test_parse_media_time_rejects_invalid_values(value: str) -> None:
    with pytest.raises(VideoExtractionError) as error:
        parse_media_time(value)
    assert error.value.code == "invalid_time_format"
    assert "小時:分鐘" in error.value.message


@pytest.mark.parametrize(
    ("start", "end", "duration", "expected"),
    [
        (None, None, 10800.0, (0.0, 10800.0, True)),
        ("", "", 10800.0, (0.0, 10800.0, True)),
        ("", "0:10", 10800.0, (0.0, 600.0, False)),
        ("0:0", "", 10800.0, (0.0, 10800.0, False)),
        ("1:00", None, 10800.0, (3600.0, 10800.0, False)),
        (None, "2:00", 10800.0, (0.0, 7200.0, False)),
        ("1:00", "2:00", 10800.0, (3600.0, 7200.0, False)),
        ("24:00", "24:59", 90000.0, (86400.0, 89940.0, False)),
    ],
)
def test_resolve_media_range(start, end, duration, expected) -> None:
    resolved = resolve_media_range(start, end, duration)
    assert (resolved.start, resolved.end, resolved.is_full) == expected


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2:00", "2:00", "晚於"),
        ("2:30", "2:00", "晚於"),
        ("4:00", None, "超出"),
        (None, "4:00", "超出"),
    ],
)
def test_resolve_media_range_rejects_invalid_bounds(start, end, message) -> None:
    with pytest.raises(VideoExtractionError) as error:
        resolve_media_range(start, end, 10800.0)
    assert error.value.code == "invalid_time_range"
    assert message in error.value.message


def test_untouched_default_end_clamps_only_for_short_media() -> None:
    assert effective_default_end_time("0:10", 120.0, True) is None
    assert effective_default_end_time("0:10", 600.0, True) == "0:10"
    assert effective_default_end_time("0:10", 1200.0, True) == "0:10"


def test_explicit_or_changed_end_time_is_never_clamped() -> None:
    assert effective_default_end_time("0:10", 120.0, False) == "0:10"
    assert effective_default_end_time("0:11", 120.0, True) == "0:11"
    assert effective_default_end_time("", 120.0, True) == ""


def test_filter_segments_clamps_overlap_and_preserves_order() -> None:
    segments = [
        {"start": 10.0, "end": 20.0, "text": "before"},
        {"start": 55.0, "end": 70.0, "text": "start overlap"},
        {"start": 80.123, "end": 90.456, "text": "inside"},
        {"start": 115.0, "end": 125.0, "text": "end overlap"},
        {"start": 130.0, "end": 140.0, "text": "after"},
    ]
    assert filter_segments_to_range(segments, 60.0, 120.0) == [
        {"start": 60.0, "end": 70.0, "text": "start overlap"},
        {"start": 80.123, "end": 90.456, "text": "inside"},
        {"start": 115.0, "end": 120.0, "text": "end overlap"},
    ]


def test_offset_clip_segments_adds_offset_once_and_retains_precision() -> None:
    segments = [{"start": 1.237, "end": 3.456, "text": "speech"}]
    assert offset_clip_segments(segments, 180.0, 240.0) == [
        {"start": 181.237, "end": 183.456, "text": "speech"}
    ]


def test_create_range_audio_clip_uses_duration_and_safe_arguments(tmp_path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "clip.wav"
    source.write_bytes(b"media")
    observed: dict[str, object] = {}

    def runner(arguments, **kwargs):
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        output.write_bytes(b"audio")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    assert create_range_audio_clip(source, output, 60.0, 120.0, runner) == output
    arguments = observed["arguments"]
    assert arguments[arguments.index("-ss") + 1] == "60.000"
    assert arguments[arguments.index("-t") + 1] == "60.000"
    assert observed["kwargs"]["shell"] is False
