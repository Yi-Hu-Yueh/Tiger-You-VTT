from html import unescape
from io import StringIO
import math
import re
from typing import Any

import webvtt
from webvtt.errors import MalformedFileError

from app.services.errors import VideoExtractionError


_WHITESPACE = re.compile(r"\s+")
_ROLLING_GAP_TOLERANCE_SECONDS = 1.0
_MIN_OVERLAP_WORDS = 2


def clean_caption_text(text: str) -> str:
    """Remove parser-recognized VTT markup and normalize presentation whitespace."""
    return _WHITESPACE.sub(" ", unescape(text)).strip()


def _timestamp_seconds(timestamp: Any) -> float:
    return round(
        timestamp.hours * 3600
        + timestamp.minutes * 60
        + timestamp.seconds
        + timestamp.milliseconds / 1000,
        3,
    )


def _overlap_size(previous: str, current: str) -> int:
    previous_words = previous.split()
    current_words = current.split()
    maximum = min(len(previous_words), len(current_words))

    for size in range(maximum, _MIN_OVERLAP_WORDS - 1, -1):
        previous_suffix = [word.casefold() for word in previous_words[-size:]]
        current_prefix = [word.casefold() for word in current_words[:size]]
        if previous_suffix == current_prefix:
            return size
    return 0


def normalize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(segments, key=lambda item: (item["start"], item["end"]))
    normalized: list[dict[str, Any]] = []
    previous_raw_text: str | None = None
    previous_end: float | None = None
    previous_emitted_text: str | None = None
    repeated_emitted_fragment = False

    for segment in ordered:
        text = clean_caption_text(str(segment["text"]))
        if not text:
            continue

        start = float(segment["start"])
        end = float(segment["end"])
        if start < 0 or end < start:
            raise VideoExtractionError(
                422,
                "malformed_vtt",
                "The downloaded VTT contains an invalid cue time range.",
            )

        raw_text = text
        is_rolling_neighbor = (
            previous_raw_text is not None
            and previous_end is not None
            and start <= previous_end + _ROLLING_GAP_TOLERANCE_SECONDS
        )
        if is_rolling_neighbor:
            if raw_text.casefold() in {
                previous_raw_text.casefold(),
                previous_emitted_text.casefold() if previous_emitted_text else "",
            }:
                repeated_emitted_fragment = (
                    previous_emitted_text is not None
                    and raw_text.casefold() == previous_emitted_text.casefold()
                )
                previous_raw_text = raw_text
                previous_end = max(previous_end, end)
                continue

            overlap = _overlap_size(previous_raw_text, raw_text)
            if not overlap and repeated_emitted_fragment:
                previous_words = previous_raw_text.split()
                current_words = raw_text.split()
                if (
                    len(previous_words) == 1
                    and len(current_words) > 1
                    and previous_words[0].casefold() == current_words[0].casefold()
                ):
                    overlap = 1
            if overlap:
                text = " ".join(raw_text.split()[overlap:]).strip()
                if not text:
                    previous_raw_text = raw_text
                    previous_end = max(previous_end, end)
                    continue

        normalized.append({"start": start, "end": end, "text": text})
        previous_emitted_text = text
        previous_raw_text = raw_text
        previous_end = end
        repeated_emitted_fragment = False

    return normalized


def parse_vtt(vtt_content: str) -> list[dict[str, Any]]:
    if not vtt_content.strip():
        raise VideoExtractionError(
            502,
            "empty_subtitle",
            "The selected subtitle track downloaded as an empty file.",
        )

    try:
        document = webvtt.from_buffer(StringIO(vtt_content))
    except (MalformedFileError, UnicodeError, ValueError) as exc:
        raise VideoExtractionError(
            502,
            "malformed_vtt",
            "The selected subtitle track is not valid, parseable VTT.",
        ) from exc

    parsed: list[dict[str, Any]] = []
    for caption in document:
        start = _timestamp_seconds(caption.start_time)
        end = _timestamp_seconds(caption.end_time)
        text = clean_caption_text(caption.text)
        if not text:
            continue
        parsed.append({"start": start, "end": end, "text": text})

    if not parsed:
        raise VideoExtractionError(
            502,
            "malformed_vtt",
            "The downloaded VTT contains no usable subtitle cues.",
        )

    return normalize_segments(parsed)


def transcript_to_txt(segments: list[dict[str, Any]]) -> str:
    return ",".join(
        segment["text"]
        for segment in segments
        if segment["text"].strip()
    )


def normalize_transcribed_segments(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate Whisper segments without YouTube rolling-caption deduplication."""
    normalized: list[dict[str, Any]] = []
    previous_start = -1.0

    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
            raw_text = segment["text"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Whisper returned a malformed segment") from exc

        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end < start
            or start < previous_start
        ):
            raise ValueError("Whisper returned an invalid segment time range")
        if not isinstance(raw_text, str):
            raise ValueError("Whisper returned non-text segment content")

        text = _WHITESPACE.sub(" ", raw_text).strip()
        previous_start = start
        if not text:
            continue
        normalized.append(
            {"start": round(start, 3), "end": round(end, 3), "text": text}
        )

    return normalized


def _vtt_timestamp(seconds: float) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def transcript_to_vtt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for segment in segments:
        blocks.append(
            "\n".join(
                (
                    f"{_vtt_timestamp(segment['start'])} --> "
                    f"{_vtt_timestamp(segment['end'])}",
                    segment["text"],
                )
            )
        )
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


def _srt_timestamp(seconds: float) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def transcript_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for number, segment in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                (
                    str(number),
                    f"{_srt_timestamp(segment['start'])} --> "
                    f"{_srt_timestamp(segment['end'])}",
                    segment["text"],
                )
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")
