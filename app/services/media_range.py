from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from app.services.errors import VideoExtractionError


_MEDIA_TIME_PATTERN = re.compile(r"^([0-9]+):([0-9]{1,2})$")


@dataclass(frozen=True)
class ResolvedMediaRange:
    start: float
    end: float
    is_full: bool


def parse_media_time(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    match = _MEDIA_TIME_PATTERN.fullmatch(value.strip())
    if match is None:
        raise VideoExtractionError(
            422,
            "invalid_time_format",
            "時間格式錯誤，請使用 小時:分鐘，例如 0:03 或 2:25",
        )
    hours = int(match.group(1))
    minutes = int(match.group(2))
    if minutes > 59:
        raise VideoExtractionError(
            422,
            "invalid_time_format",
            "時間格式錯誤，請使用 小時:分鐘，例如 0:03 或 2:25",
        )
    return float(hours * 3600 + minutes * 60)


def resolve_media_range(
    start_time: str | None,
    end_time: str | None,
    duration: float | None,
) -> ResolvedMediaRange:
    parsed_start = parse_media_time(start_time)
    parsed_end = parse_media_time(end_time)
    if duration is None or not math.isfinite(duration) or duration <= 0:
        if parsed_start is None and parsed_end is None:
            raise VideoExtractionError(
                422,
                "media_duration_unavailable",
                "無法取得影片長度，因此無法解析擷取區間",
            )
        raise VideoExtractionError(
            422,
            "media_duration_unavailable",
            "無法取得影片長度，請確認影片可正常讀取",
        )

    range_start = 0.0 if parsed_start is None else parsed_start
    range_end = float(duration) if parsed_end is None else parsed_end
    if range_start >= duration or range_end > duration:
        raise VideoExtractionError(
            422,
            "invalid_time_range",
            "擷取時間超出影片長度",
        )
    if range_end <= range_start:
        raise VideoExtractionError(
            422,
            "invalid_time_range",
            "擷取結束時間必須晚於擷取開始時間",
        )
    return ResolvedMediaRange(
        start=range_start,
        end=range_end,
        is_full=parsed_start is None and parsed_end is None,
    )


def filter_segments_to_range(
    segments: list[dict[str, Any]],
    range_start: float,
    range_end: float,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        if end <= range_start or start >= range_end:
            continue
        clamped_start = max(start, range_start)
        clamped_end = min(end, range_end)
        if clamped_end <= clamped_start:
            continue
        selected.append(
            {
                "start": round(clamped_start, 3),
                "end": round(clamped_end, 3),
                "text": segment["text"],
            }
        )
    return selected


def offset_clip_segment(
    segment: dict[str, Any],
    range_start: float,
    range_end: float,
) -> dict[str, Any] | None:
    absolute = {
        "start": round(float(segment["start"]) + range_start, 3),
        "end": round(float(segment["end"]) + range_start, 3),
        "text": segment["text"],
    }
    selected = filter_segments_to_range([absolute], range_start, range_end)
    return selected[0] if selected else None


def offset_clip_segments(
    segments: list[dict[str, Any]],
    range_start: float,
    range_end: float,
) -> list[dict[str, Any]]:
    offset: list[dict[str, Any]] = []
    for segment in segments:
        translated = offset_clip_segment(segment, range_start, range_end)
        if translated is not None:
            offset.append(translated)
    return offset


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def create_range_audio_clip(
    source_path: Path,
    output_path: Path,
    range_start: float,
    range_end: float,
    runner: CommandRunner | None = None,
) -> Path:
    command_runner = runner or subprocess.run
    clip_duration = range_end - range_start
    arguments = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-ss",
        f"{range_start:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{clip_duration:.3f}",
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    try:
        completed = command_runner(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=1800,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VideoExtractionError(
            503,
            "range_clip_failed",
            "找不到 FFmpeg，無法建立指定擷取區間",
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoExtractionError(
            502,
            "range_clip_failed",
            "無法安全建立指定擷取區間",
        ) from exc
    if completed.returncode != 0 or not output_path.is_file():
        raise VideoExtractionError(
            502,
            "range_clip_failed",
            "FFmpeg 無法建立指定擷取區間",
        )
    return output_path
