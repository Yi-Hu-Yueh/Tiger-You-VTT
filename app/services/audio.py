from collections.abc import Callable
import math
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import UploadFile

from app.config import VIDEO_UPLOAD_SETTINGS, VideoUploadSettings
from app.services.errors import VideoExtractionError
from app.services.media_range import (
    create_range_audio_clip,
    effective_default_end_time,
    offset_clip_segment,
    offset_clip_segments,
    resolve_media_range,
)
from app.services.transcript import (
    transcript_to_srt,
    transcript_to_txt,
    transcript_to_vtt,
)
from app.services.video import MediaInfo, probe_media_info, store_upload


SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


def safe_audio_upload_name(filename: str | None) -> tuple[str, str]:
    raw_name = (filename or "").replace("\\", "/")
    display_name = PurePosixPath(raw_name).name.strip()
    suffix = PurePosixPath(display_name).suffix.casefold()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise VideoExtractionError(
            422,
            "unsupported_audio_format",
            "The uploaded filename does not use a supported audio extension.",
        )
    return display_name, suffix


async def store_audio_upload(
    upload: UploadFile,
    destination: Path,
    settings: VideoUploadSettings = VIDEO_UPLOAD_SETTINGS,
) -> int:
    return await store_upload(
        upload,
        destination,
        settings,
        invalid_code="invalid_audio_file",
        media_label="audio",
    )


def probe_audio(media_path: Path) -> MediaInfo:
    media = probe_media_info(
        media_path,
        invalid_code="invalid_audio_file",
        invalid_message="The upload is not a valid, supported audio file.",
    )
    if not media.has_audio:
        raise VideoExtractionError(
            422,
            "no_audio_available",
            "The uploaded file does not contain a usable audio stream.",
        )
    if media.has_video:
        raise VideoExtractionError(
            422,
            "invalid_audio_file",
            "The uploaded file contains video; use the video upload endpoint.",
        )
    if (
        media.duration is None
        or not math.isfinite(media.duration)
        or media.duration <= 0
    ):
        raise VideoExtractionError(
            422,
            "invalid_audio_file",
            "The uploaded audio duration is unavailable or invalid.",
        )
    return media


def _transcribe_audio(
    audio_path: Path,
    on_segment: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Any:
    from app.services.whisper import transcribe_audio

    if on_segment is None and should_stop is None:
        return transcribe_audio(audio_path)
    return transcribe_audio(audio_path, on_segment, should_stop)


def _stopped_result(
    filename: str,
    duration: float,
    range_start: float,
    range_end: float,
) -> dict[str, Any]:
    return {
        "_stopped": True,
        "filename": filename,
        "language": "und",
        "type": "transcribed",
        "selection_mode": "direct",
        "segment_count": 0,
        "duration": duration,
        "range_start": range_start,
        "range_end": range_end,
        "segments": [],
        "vtt": transcript_to_vtt([]),
        "txt": "",
        "srt": "",
    }


def process_uploaded_audio(
    media_path: Path,
    filename: str,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    end_time_is_default: bool = False,
    on_segment: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_range_resolved: Callable[[float, float], None] | None = None,
) -> dict[str, Any]:
    media = probe_audio(media_path)
    resolved_end_time = effective_default_end_time(
        end_time, media.duration, end_time_is_default
    )
    media_range = resolve_media_range(
        start_time, resolved_end_time, media.duration
    )
    if on_range_resolved is not None:
        on_range_resolved(media_range.start, media_range.end)

    duration = float(media.duration)
    if should_stop is not None and should_stop():
        return _stopped_result(
            filename, duration, media_range.start, media_range.end
        )

    transcription_source = media_path
    range_directory: TemporaryDirectory[str] | None = None
    ranged_on_segment = on_segment
    try:
        if not media_range.is_full:
            range_directory = TemporaryDirectory(
                prefix="tiger-you-vtt-audio-range-"
            )
            transcription_source = create_range_audio_clip(
                media_path,
                Path(range_directory.name) / "range.wav",
                media_range.start,
                media_range.end,
            )
            if should_stop is not None and should_stop():
                return _stopped_result(
                    filename, duration, media_range.start, media_range.end
                )
            if on_segment is not None:

                def ranged_on_segment(segment: dict[str, Any]) -> None:
                    translated = offset_clip_segment(
                        segment, media_range.start, media_range.end
                    )
                    if translated is not None:
                        on_segment(translated)

        transcription = _transcribe_audio(
            transcription_source, ranged_on_segment, should_stop
        )
    finally:
        if range_directory is not None:
            range_directory.cleanup()

    segments = (
        transcription.segments
        if media_range.is_full
        else offset_clip_segments(
            transcription.segments, media_range.start, media_range.end
        )
    )
    result = {
        "filename": filename,
        "language": transcription.language,
        "type": "transcribed",
        "selection_mode": "direct",
        "segment_count": len(segments),
        "duration": media.duration,
        "range_start": media_range.start,
        "range_end": media_range.end,
        "segments": segments,
        "vtt": transcript_to_vtt(segments),
        "txt": transcript_to_txt(segments),
        "srt": transcript_to_srt(segments),
        "transcription_model": transcription.model,
        "transcription_device": transcription.device,
        "transcription_compute_type": transcription.compute_type,
        "transcription_duration": transcription.duration_seconds,
    }
    if transcription.stopped:
        result["_stopped"] = True
    return result
