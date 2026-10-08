from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import logging
import re
from typing import Any, Literal

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.utils import DownloadError

from app.config import (
    YOUTUBE_SEARCH_CANDIDATE_LIMIT,
    YOUTUBE_SEARCH_DEFAULT_LIMIT,
    YOUTUBE_SEARCH_MAX_LIMIT,
)
from app.services.errors import VideoExtractionError
from app.services.youtube import YDL_OPTIONS
from app.services.youtube_auth import extractor, with_auth_fallback


YouTubeSearchSort = Literal[
    "relevance", "upload_date", "view_count", "duration"
]
SUPPORTED_SORTS: tuple[YouTubeSearchSort, ...] = (
    "relevance",
    "upload_date",
    "view_count",
    "duration",
)
MAX_QUERY_LENGTH = 200
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
logger = logging.getLogger(__name__)


def _supports_native_date_search() -> bool:
    probe = "ytsearchdate1:test"
    for extractor_class in gen_extractor_classes():
        ie_name = str(getattr(extractor_class, "IE_NAME", "")).casefold()
        if "youtube" not in ie_name or "search" not in ie_name:
            continue
        try:
            if extractor_class.suitable(probe):
                return True
        except Exception:
            continue
    return False


NATIVE_DATE_SEARCH_SUPPORTED = _supports_native_date_search()


def format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _optional_text(info: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value)


def _normalize_upload_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None
    return parsed.date().isoformat()


def _normalize_result(info: Mapping[str, Any]) -> dict[str, Any] | None:
    video_id = _optional_text(info, "id")
    title = _optional_text(info, "title")
    if (
        video_id is None
        or title is None
        or _VIDEO_ID_PATTERN.fullmatch(video_id) is None
    ):
        return None
    duration = _optional_nonnegative_int(info.get("duration"))
    view_count = _optional_nonnegative_int(info.get("view_count"))
    return {
        "rank": 0,
        "video_id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "channel": _optional_text(info, "channel", "uploader"),
        "duration": duration,
        "duration_text": format_duration(duration),
        "view_count": view_count,
        "upload_date": _normalize_upload_date(info.get("upload_date")),
        "thumbnail": _optional_text(info, "thumbnail"),
    }


def _validate_request(
    query: str, sort: str, limit: int
) -> tuple[str, YouTubeSearchSort, int]:
    normalized_query = query.strip()
    if not normalized_query:
        raise VideoExtractionError(
            422,
            "youtube_search_query_required",
            "A YouTube search query is required.",
        )
    if len(normalized_query) > MAX_QUERY_LENGTH:
        raise VideoExtractionError(
            422,
            "youtube_search_query_invalid",
            f"The YouTube search query must not exceed {MAX_QUERY_LENGTH} characters.",
        )
    if sort not in SUPPORTED_SORTS:
        raise VideoExtractionError(
            422,
            "youtube_search_invalid_sort",
            "The requested YouTube search ranking is unsupported.",
        )
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise VideoExtractionError(
            422,
            "youtube_search_invalid_limit",
            "The YouTube search limit must be an integer.",
        )
    if not 1 <= limit <= YOUTUBE_SEARCH_MAX_LIMIT:
        raise VideoExtractionError(
            422,
            "youtube_search_invalid_limit",
            f"The YouTube search limit must be between 1 and {YOUTUBE_SEARCH_MAX_LIMIT}.",
        )
    return normalized_query, sort, limit


def _search_target(
    query: str, sort: YouTubeSearchSort, candidate_limit: int
) -> tuple[str, bool]:
    if sort == "upload_date" and NATIVE_DATE_SEARCH_SUPPORTED:
        return f"ytsearchdate{candidate_limit}:{query}", True
    return f"ytsearch{candidate_limit}:{query}", False


def _youtube_options(*, flat: bool, candidate_limit: int) -> dict[str, Any]:
    return {
        **YDL_OPTIONS,
        "noplaylist": not flat,
        "skip_download": True,
        "playlistend": candidate_limit,
        "extract_flat": "in_playlist" if flat else False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writethumbnail": False,
    }


def _extract_search_candidates(
    target: str,
    candidate_limit: int,
    ydl_factory: Callable[[dict[str, Any]], Any],
) -> list[Mapping[str, Any]]:
    def attempt(cookie_file):
        with extractor(ydl_factory, _youtube_options(flat=True, candidate_limit=candidate_limit), cookie_file) as ydl:
            extracted = ydl.extract_info(target, download=False)
            if not isinstance(extracted, Mapping):
                raise VideoExtractionError(502, "youtube_search_failed", "YouTube returned an invalid search response.")
            # Materialize lazy entries inside the retry boundary and context.
            entries = extracted.get("entries")
            return [entry for entry in ([] if entries is None else entries) if isinstance(entry, Mapping)]

    try:
        return with_auth_fallback(attempt)
    except VideoExtractionError:
        raise
    except DownloadError as exc:
        raise VideoExtractionError(
            502,
            "youtube_search_failed",
            "YouTube search metadata could not be retrieved.",
        ) from exc
    except Exception as exc:
        raise VideoExtractionError(
            502,
            "youtube_search_failed",
            "YouTube search failed unexpectedly.",
        ) from exc

def _merge_enriched_result(
    base: dict[str, Any], enriched: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(enriched)
    for key in (
        "title",
        "channel",
        "duration",
        "duration_text",
        "view_count",
        "upload_date",
        "thumbnail",
    ):
        if merged.get(key) is None and base.get(key) is not None:
            merged[key] = base[key]
    return merged


def _enrich_candidates(
    candidates: list[dict[str, Any]],
    candidate_limit: int,
    ydl_factory: Callable[[dict[str, Any]], Any],
    result_limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    enriched_candidates: list[dict[str, Any]] = []
    skipped_count = 0
    options = _youtube_options(flat=False, candidate_limit=candidate_limit)
    try:
        with extractor(ydl_factory, options) as ydl:
            for candidate in candidates:
                def attempt(cookie_file):
                    if cookie_file is None:
                        return ydl.extract_info(candidate["url"], download=False)
                    with extractor(ydl_factory, options, cookie_file) as authenticated:
                        return authenticated.extract_info(candidate["url"], download=False)

                try:
                    info = with_auth_fallback(attempt)
                except VideoExtractionError:
                    raise
                except Exception:
                    logger.warning(
                        "Skipping unavailable YouTube search candidate %s",
                        candidate["video_id"],
                        exc_info=True,
                    )
                    skipped_count += 1
                    continue
                if not isinstance(info, Mapping):
                    skipped_count += 1
                    continue
                enriched = _normalize_result(info)
                if enriched is None:
                    skipped_count += 1
                    continue
                enriched_candidates.append(
                    _merge_enriched_result(candidate, enriched)
                )
                if (
                    result_limit is not None
                    and len(enriched_candidates) >= result_limit
                ):
                    break
    except VideoExtractionError:
        raise
    except Exception as exc:
        raise VideoExtractionError(
            502,
            "youtube_search_failed",
            "YouTube candidate metadata enrichment could not be started.",
        ) from exc
    return enriched_candidates, skipped_count


def _sort_results(
    results: list[dict[str, Any]], sort: YouTubeSearchSort
) -> list[dict[str, Any]]:
    if sort == "relevance":
        return results
    if sort == "upload_date":
        return sorted(
            results,
            key=lambda item: (
                item["upload_date"] is None,
                0
                if item["upload_date"] is None
                else -int(item["upload_date"].replace("-", "")),
            ),
        )
    if sort == "view_count":
        return sorted(
            results,
            key=lambda item: (
                item["view_count"] is None,
                0 if item["view_count"] is None else -item["view_count"],
            ),
        )
    return sorted(
        results,
        key=lambda item: (
            item["duration"] is None,
            0 if item["duration"] is None else item["duration"],
        ),
    )
def search_youtube(
    query: str,
    sort: str = "relevance",
    limit: int = YOUTUBE_SEARCH_DEFAULT_LIMIT,
    *,
    ydl_factory: Callable[[dict[str, Any]], Any] = yt_dlp.YoutubeDL,
) -> dict[str, Any]:
    normalized_query, normalized_sort, normalized_limit = _validate_request(
        query, sort, limit
    )
    candidate_limit = max(normalized_limit, YOUTUBE_SEARCH_CANDIDATE_LIMIT)
    target, native_date_order = _search_target(
        normalized_query, normalized_sort, candidate_limit
    )
    entries = _extract_search_candidates(target, candidate_limit, ydl_factory)
    normalized = [
        result
        for entry in entries
        if (result := _normalize_result(entry)) is not None
    ]
    candidate_count = len(normalized)
    skipped_count = 0
    enriched_count = candidate_count
    sortable = normalized
    if normalized:
        sortable, skipped_count = _enrich_candidates(
            normalized,
            candidate_limit,
            ydl_factory,
            normalized_limit if normalized_sort == "relevance" else None,
        )
        enriched_count = len(sortable)
        if not sortable:
            raise VideoExtractionError(
                502,
                "youtube_search_failed",
                "No usable YouTube search candidates could be resolved.",
            )
    ordered = (
        sortable
        if normalized_sort == "upload_date" and native_date_order
        else _sort_results(sortable, normalized_sort)
    )
    limited = ordered[:normalized_limit]
    for rank, result in enumerate(limited, start=1):
        result["rank"] = rank
    return {
        "query": normalized_query,
        "sort": normalized_sort,
        "limit": normalized_limit,
        "candidate_count": candidate_count,
        "enriched_count": enriched_count,
        "skipped_count": skipped_count,
        "native_date_order": native_date_order,
        "results": limited,
    }
