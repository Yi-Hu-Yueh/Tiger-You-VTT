from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any

from fastapi import UploadFile

from app.config import VIDEO_UPLOAD_SETTINGS, VideoUploadSettings
from app.services.errors import VideoExtractionError
from app.services.transcript import (
    parse_vtt,
    transcript_to_srt,
    transcript_to_txt,
    transcript_to_vtt,
)


SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".mpeg",
    ".mpg",
}

TEXT_SUBTITLE_CODECS = {
    "ass",
    "eia_608",
    "eia_708",
    "jacosub",
    "microdvd",
    "mov_text",
    "mpl2",
    "pjs",
    "realtext",
    "sami",
    "ssa",
    "subrip",
    "subviewer",
    "subviewer1",
    "text",
    "vplayer",
    "webvtt",
}


@dataclass(frozen=True)
class MediaStream:
    index: int
    codec_type: str
    codec_name: str
    language: str | None = None
    is_default: bool = False

    @property
    def is_text_subtitle(self) -> bool:
        return (
            self.codec_type == "subtitle"
            and self.codec_name.casefold() in TEXT_SUBTITLE_CODECS
        )


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None
    streams: tuple[MediaStream, ...]

    @property
    def has_video(self) -> bool:
        return any(stream.codec_type == "video" for stream in self.streams)

    @property
    def has_audio(self) -> bool:
        return any(stream.codec_type == "audio" for stream in self.streams)

    @property
    def text_subtitles(self) -> tuple[MediaStream, ...]:
        return tuple(stream for stream in self.streams if stream.is_text_subtitle)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def safe_upload_name(filename: str | None) -> tuple[str, str]:
    raw_name = (filename or "").replace("\\", "/")
    display_name = PurePosixPath(raw_name).name.strip()
    suffix = PurePosixPath(display_name).suffix.casefold()
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise VideoExtractionError(
            422,
            "unsupported_video_format",
            "The uploaded filename does not use a supported video extension.",
        )
    return display_name, suffix


async def store_upload(
    upload: UploadFile,
    destination: Path,
    settings: VideoUploadSettings = VIDEO_UPLOAD_SETTINGS,
) -> int:
    total_bytes = 0
    try:
        with destination.open("xb") as output:
            while True:
                chunk = await upload.read(settings.chunk_bytes)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.max_bytes:
                    raise VideoExtractionError(
                        413,
                        "upload_too_large",
                        "The uploaded video exceeds the configured size limit.",
                    )
                output.write(chunk)
    except VideoExtractionError:
        raise
    except Exception as exc:
        raise VideoExtractionError(
            500,
            "upload_storage_failed",
            "The uploaded video could not be stored temporarily.",
        ) from exc

    if total_bytes == 0:
        raise VideoExtractionError(
            422,
            "invalid_video_file",
            "The uploaded video file is empty or invalid.",
        )
    return total_bytes


def _duration_from_probe(payload: Mapping[str, Any]) -> float | None:
    raw_duration: Any = None
    format_info = payload.get("format")
    if isinstance(format_info, Mapping):
        raw_duration = format_info.get("duration")

    if raw_duration is None:
        streams = payload.get("streams")
        if isinstance(streams, list):
            durations = []
            for stream in streams:
                if not isinstance(stream, Mapping):
                    continue
                try:
                    duration = float(stream.get("duration"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(duration) and duration >= 0:
                    durations.append(duration)
            return max(durations, default=None)

    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration >= 0 else None


def _stream_from_probe(raw_stream: Mapping[str, Any]) -> MediaStream | None:
    index = raw_stream.get("index")
    codec_type = raw_stream.get("codec_type")
    codec_name = raw_stream.get("codec_name")
    if (
        not isinstance(index, int)
        or not isinstance(codec_type, str)
        or not isinstance(codec_name, str)
    ):
        return None

    tags = raw_stream.get("tags")
    raw_language = tags.get("language") if isinstance(tags, Mapping) else None
    language = (
        raw_language.strip()
        if isinstance(raw_language, str) and raw_language.strip()
        else None
    )
    disposition = raw_stream.get("disposition")
    is_default = (
        bool(disposition.get("default"))
        if isinstance(disposition, Mapping)
        else False
    )
    return MediaStream(index, codec_type, codec_name, language, is_default)


def probe_media(
    media_path: Path, runner: CommandRunner | None = None
) -> MediaInfo:
    command_runner = runner or subprocess.run
    arguments = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    try:
        completed = command_runner(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VideoExtractionError(
            503,
            "media_probe_failed",
            "ffprobe is not available in the local runtime.",
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoExtractionError(
            502,
            "media_probe_failed",
            "The uploaded media could not be inspected safely.",
        ) from exc

    if completed.returncode != 0:
        raise VideoExtractionError(
            422,
            "invalid_video_file",
            "The upload is not a valid, supported video file.",
        )

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise VideoExtractionError(
            502,
            "media_probe_failed",
            "ffprobe returned an invalid structured response.",
        ) from exc
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("streams"), list
    ):
        raise VideoExtractionError(
            502,
            "media_probe_failed",
            "ffprobe returned incomplete stream metadata.",
        )

    streams = tuple(
        stream
        for raw_stream in payload["streams"]
        if isinstance(raw_stream, Mapping)
        and (stream := _stream_from_probe(raw_stream)) is not None
    )
    media = MediaInfo(_duration_from_probe(payload), streams)
    if not media.has_video:
        raise VideoExtractionError(
            422,
            "invalid_video_file",
            "The upload does not contain a usable video stream.",
        )
    return media


def _language_priority(language: str | None) -> int:
    normalized = (language or "").strip().replace("_", "-").casefold()
    if normalized in {"zh-tw", "zh-hant"} or normalized.startswith("zh-hant-"):
        return 0
    if normalized in {"zh", "zh-hans", "zho", "chi", "cmn"}:
        return 1
    if normalized in {"en", "eng"}:
        return 2
    return 3


def select_embedded_subtitle(media: MediaInfo) -> MediaStream | None:
    tracks = media.text_subtitles
    if not tracks:
        return None
    return min(
        tracks,
        key=lambda stream: (
            0 if stream.is_default else 1,
            _language_priority(stream.language),
            stream.index,
        ),
    )


def extract_embedded_vtt(
    media_path: Path,
    stream: MediaStream,
    output_path: Path,
    runner: CommandRunner | None = None,
) -> str:
    command_runner = runner or subprocess.run
    arguments = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(media_path),
        "-map",
        f"0:{stream.index}",
        "-c:s",
        "webvtt",
        "-f",
        "webvtt",
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
            timeout=300,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VideoExtractionError(
            503,
            "embedded_subtitle_extraction_failed",
            "ffmpeg is not available in the local runtime.",
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoExtractionError(
            502,
            "embedded_subtitle_extraction_failed",
            "The embedded subtitle could not be extracted safely.",
        ) from exc

    if completed.returncode != 0 or not output_path.is_file():
        raise VideoExtractionError(
            502,
            "embedded_subtitle_extraction_failed",
            "ffmpeg could not convert the selected embedded subtitle to WebVTT.",
        )
    try:
        return output_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise VideoExtractionError(
            502,
            "embedded_subtitle_extraction_failed",
            "The extracted embedded subtitle could not be read safely.",
        ) from exc


def _transcribe_local_media(
    media_path: Path,
    on_segment: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Any:
    from app.services.whisper import transcribe_audio

    if on_segment is None and should_stop is None:
        return transcribe_audio(media_path)
    return transcribe_audio(media_path, on_segment, should_stop)


def process_uploaded_video(
    media_path: Path,
    filename: str,
    working_directory: Path,
    *,
    on_segment: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    media = probe_media(media_path)
    selected = select_embedded_subtitle(media)

    if selected is not None:
        extracted_path = working_directory / "embedded.vtt"
        extracted_vtt = extract_embedded_vtt(
            media_path, selected, extracted_path
        )
        try:
            segments = parse_vtt(extracted_vtt)
        except VideoExtractionError as exc:
            raise VideoExtractionError(
                502,
                "embedded_subtitle_extraction_failed",
                "The extracted embedded subtitle was not valid WebVTT.",
            ) from exc
        language = selected.language or "und"
        return {
            "filename": filename,
            "language": language,
            "type": "embedded",
            "selection_mode": "auto",
            "segment_count": len(segments),
            "duration": media.duration,
            "segments": segments,
            "vtt": transcript_to_vtt(segments),
            "txt": transcript_to_txt(segments),
            "srt": transcript_to_srt(segments),
        }

    if not media.has_audio:
        raise VideoExtractionError(
            422,
            "no_transcript_source_available",
            "The uploaded video contains neither a usable text subtitle nor audio.",
        )

    if should_stop is not None and should_stop():
        return {
            "_stopped": True,
            "filename": filename,
            "language": "und",
            "type": "transcribed",
            "selection_mode": "fallback",
            "segment_count": 0,
            "duration": media.duration,
            "segments": [],
            "vtt": transcript_to_vtt([]),
            "txt": "",
            "srt": "",
        }

    if on_segment is None and should_stop is None:
        transcription = _transcribe_local_media(media_path)
    else:
        transcription = _transcribe_local_media(
            media_path, on_segment, should_stop
        )
    segments = transcription.segments
    result = {
        "filename": filename,
        "language": transcription.language,
        "type": "transcribed",
        "selection_mode": "fallback",
        "segment_count": len(segments),
        "duration": media.duration,
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
