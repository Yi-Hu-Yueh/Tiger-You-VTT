from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import yt_dlp
from yt_dlp.utils import DownloadError, UnsupportedError


YDL_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "noprogress": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "socket_timeout": 15,
}


class VideoExtractionError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SelectedSubtitleTrack:
    language: str
    type: Literal["manual", "auto"]
    selection_mode: Literal["auto", "explicit"]


def _normalize_tracks(
    tracks: object, track_type: Literal["manual", "auto"]
) -> list[dict[str, Any]]:
    if not isinstance(tracks, Mapping):
        return []

    normalized: list[dict[str, Any]] = []
    for language, raw_formats in tracks.items():
        if not isinstance(language, str) or not isinstance(raw_formats, list):
            continue

        formats: list[dict[str, Any]] = []
        seen_extensions: set[str] = set()
        name: str | None = None

        for raw_format in raw_formats:
            if not isinstance(raw_format, Mapping):
                continue
            if name is None and isinstance(raw_format.get("name"), str):
                name = raw_format["name"]

            ext = raw_format.get("ext")
            if not isinstance(ext, str) or not ext or ext in seen_extensions:
                continue
            seen_extensions.add(ext)
            formats.append(
                {
                    "ext": ext,
                    "url_available": bool(raw_format.get("url")),
                }
            )

        if formats:
            normalized.append(
                {
                    "language": language,
                    "type": track_type,
                    "name": name or language,
                    "formats": formats,
                }
            )

    return normalized


def _required_text(info: Mapping[str, Any], key: str, label: str) -> str:
    value = info.get(key)
    if not isinstance(value, str) or not value:
        raise VideoExtractionError(
            status_code=502,
            code="invalid_extractor_response",
            message=f"YouTube did not return a usable {label}.",
        )
    return value


def _optional_text(info: Mapping[str, Any], key: str) -> str | None:
    value = info.get(key)
    return value if isinstance(value, str) and value else None


def normalize_video_info(info: Mapping[str, Any]) -> dict[str, Any]:
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries") is not None:
        raise VideoExtractionError(
            status_code=422,
            code="not_a_video",
            message="The supplied URL does not identify a single YouTube video.",
        )

    duration = info.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        normalized_duration = None
    else:
        normalized_duration = int(duration)

    webpage_url = _optional_text(info, "webpage_url") or _optional_text(
        info, "original_url"
    )
    if webpage_url is None:
        raise VideoExtractionError(
            status_code=502,
            code="invalid_extractor_response",
            message="YouTube did not return a usable webpage URL.",
        )

    return {
        "video_id": _required_text(info, "id", "video ID"),
        "title": _required_text(info, "title", "title"),
        "channel": _optional_text(info, "channel"),
        "uploader": _optional_text(info, "uploader"),
        "duration": normalized_duration,
        "webpage_url": webpage_url,
        "original_url": _optional_text(info, "original_url"),
        "thumbnail": _optional_text(info, "thumbnail"),
        "language": _optional_text(info, "language"),
        "subtitles": _normalize_tracks(info.get("subtitles"), "manual"),
        "automatic_captions": _normalize_tracks(
            info.get("automatic_captions"), "auto"
        ),
    }


def get_video_info(url: str) -> dict[str, Any]:
    info = _extract_raw_info(url)
    return normalize_video_info(info)


def _extract_raw_info(url: str) -> Mapping[str, Any]:
    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
    except UnsupportedError as exc:
        raise VideoExtractionError(
            status_code=422,
            code="unsupported_url",
            message="The supplied URL is not a supported YouTube video URL.",
        ) from exc
    except DownloadError as exc:
        raise VideoExtractionError(
            status_code=502,
            code="youtube_extraction_failed",
            message=(
                "YouTube metadata could not be extracted. The video may be unavailable, "
                "private, deleted, age-restricted, or temporarily inaccessible."
            ),
        ) from exc
    except Exception as exc:
        raise VideoExtractionError(
            status_code=502,
            code="youtube_extraction_failed",
            message="YouTube metadata extraction failed unexpectedly.",
        ) from exc

    if not isinstance(info, Mapping):
        raise VideoExtractionError(
            status_code=502,
            code="invalid_extractor_response",
            message="YouTube returned an invalid metadata response.",
        )

    return info


def _validate_selected_track(
    info: Mapping[str, Any], language: str, track_type: Literal["manual", "auto"]
) -> None:
    collection_name = "subtitles" if track_type == "manual" else "automatic_captions"
    tracks = info.get(collection_name)
    if not isinstance(tracks, Mapping) or language not in tracks:
        raise VideoExtractionError(
            422,
            "subtitle_track_not_found",
            f'The requested {track_type} subtitle track "{language}" does not exist.',
        )

    if not _has_usable_vtt(tracks[language]):
        raise VideoExtractionError(
            422,
            "vtt_unavailable",
            f'The requested {track_type} subtitle track "{language}" has no usable VTT format.',
        )


def _has_usable_vtt(formats: object) -> bool:
    return isinstance(formats, list) and any(
        isinstance(item, Mapping)
        and item.get("ext") == "vtt"
        and bool(item.get("url"))
        for item in formats
    )


def _track_is_marked_original(formats: object) -> bool:
    if not isinstance(formats, list):
        return False
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("name"), str)
        and "original" in item["name"].casefold()
        for item in formats
    )


def _language_sort_key(language: str) -> tuple[str, str]:
    return language.casefold(), language


def select_subtitle_track(info: Mapping[str, Any]) -> SelectedSubtitleTrack:
    """Choose one source track using a stable quality-first policy."""
    raw_manual = info.get("subtitles")
    manual = raw_manual if isinstance(raw_manual, Mapping) else {}
    usable_manual = {
        language: formats
        for language, formats in manual.items()
        if isinstance(language, str) and _has_usable_vtt(formats)
    }

    for language in ("zh-TW", "zh-Hant", "zh", "en"):
        if language in usable_manual:
            return SelectedSubtitleTrack(language, "manual", "auto")

    video_language = info.get("language")
    if isinstance(video_language, str) and video_language in usable_manual:
        return SelectedSubtitleTrack(video_language, "manual", "auto")

    if usable_manual:
        language = sorted(usable_manual, key=_language_sort_key)[0]
        return SelectedSubtitleTrack(language, "manual", "auto")

    raw_auto = info.get("automatic_captions")
    automatic = raw_auto if isinstance(raw_auto, Mapping) else {}
    usable_auto = {
        language: formats
        for language, formats in automatic.items()
        if isinstance(language, str) and _has_usable_vtt(formats)
    }

    original_auto = {
        language: formats
        for language, formats in usable_auto.items()
        if language.casefold().endswith("-orig")
        or _track_is_marked_original(formats)
    }

    preferred_chinese_originals = (
        "zh-TW-orig",
        "zh-Hant-orig",
        "zh-orig",
        "zh-Hans-orig",
    )
    for language in preferred_chinese_originals:
        if language in original_auto:
            return SelectedSubtitleTrack(language, "auto", "auto")

    other_chinese_originals = sorted(
        (
            language
            for language in original_auto
            if language.casefold().startswith("zh-")
        ),
        key=_language_sort_key,
    )
    if other_chinese_originals:
        return SelectedSubtitleTrack(other_chinese_originals[0], "auto", "auto")

    if "en-orig" in original_auto:
        return SelectedSubtitleTrack("en-orig", "auto", "auto")

    if isinstance(video_language, str):
        for language in (f"{video_language}-orig", video_language):
            if language in original_auto:
                return SelectedSubtitleTrack(language, "auto", "auto")

    if original_auto:
        language = sorted(original_auto, key=_language_sort_key)[0]
        return SelectedSubtitleTrack(language, "auto", "auto")

    if isinstance(video_language, str) and video_language in usable_auto:
        return SelectedSubtitleTrack(video_language, "auto", "auto")

    if usable_auto:
        language = sorted(usable_auto, key=_language_sort_key)[0]
        return SelectedSubtitleTrack(language, "auto", "auto")

    raise VideoExtractionError(
        422,
        "no_subtitles_available",
        "No usable YouTube subtitles are available for this video.",
    )


def _download_selected_vtt(
    url: str,
    language: str,
    track_type: Literal["manual", "auto"],
    directory: Path,
) -> str:
    options = {
        **YDL_OPTIONS,
        "outtmpl": str(directory / "subtitle.%(ext)s"),
        "writesubtitles": track_type == "manual",
        "writeautomaticsub": track_type == "auto",
        "subtitleslangs": [language],
        "subtitlesformat": "vtt",
        "nopart": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise VideoExtractionError(
            502,
            "subtitle_download_failed",
            "yt-dlp could not download the selected subtitle track.",
        ) from exc
    except Exception as exc:
        raise VideoExtractionError(
            502,
            "subtitle_download_failed",
            "The selected subtitle track could not be downloaded.",
        ) from exc

    vtt_files = list(directory.glob("*.vtt"))
    if len(vtt_files) != 1:
        raise VideoExtractionError(
            502,
            "subtitle_download_failed",
            "yt-dlp did not produce exactly one VTT subtitle file.",
        )

    try:
        return vtt_files[0].read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise VideoExtractionError(
            502,
            "subtitle_download_failed",
            "The downloaded subtitle file could not be read safely.",
        ) from exc


def get_subtitle(
    url: str,
    language: str | None = None,
    track_type: Literal["manual", "auto"] | None = None,
) -> dict[str, Any]:
    from app.services.transcript import parse_vtt, transcript_to_srt, transcript_to_txt

    info = _extract_raw_info(url)
    if (language is None) != (track_type is None):
        raise VideoExtractionError(
            422,
            "invalid_subtitle_selection",
            "language and type must either both be provided or both be omitted.",
        )

    if language is None or track_type is None:
        selected = select_subtitle_track(info)
    else:
        _validate_selected_track(info, language, track_type)
        selected = SelectedSubtitleTrack(language, track_type, "explicit")

    with TemporaryDirectory(prefix="tiger-you-vtt-") as temporary_directory:
        vtt_content = _download_selected_vtt(
            url, selected.language, selected.type, Path(temporary_directory)
        )
        segments = parse_vtt(vtt_content)

    duration = info.get("duration")
    normalized_duration = (
        float(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else None
    )
    return {
        "video_id": _required_text(info, "id", "video ID"),
        "title": _required_text(info, "title", "title"),
        "language": selected.language,
        "type": selected.type,
        "selection_mode": selected.selection_mode,
        "segment_count": len(segments),
        "duration": normalized_duration,
        "segments": segments,
        "vtt": vtt_content,
        "txt": transcript_to_txt(segments),
        "srt": transcript_to_srt(segments),
    }

