from collections.abc import Mapping

import pytest
from yt_dlp.utils import DownloadError

from app.config import YOUTUBE_SEARCH_CANDIDATE_LIMIT
from app.services.errors import VideoExtractionError
from app.services import youtube_search as search_service
from app.services.youtube_search import format_duration, search_youtube


def entry(
    video_id: str,
    *,
    title: str | None = None,
    duration=60,
    view_count=100,
    upload_date="20260101",
):
    return {
        "id": video_id,
        "title": title or f"Video {video_id}",
        "channel": "Channel",
        "duration": duration,
        "view_count": view_count,
        "upload_date": upload_date,
    }


class FakeYDL:
    def __init__(self, factory, options):
        self.factory = factory
        self.options = options
        self.targets = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def extract_info(self, target, download=False):
        self.targets.append((target, download))
        if target.startswith("ytsearch"):
            if self.factory.error is not None:
                raise self.factory.error
            return self.factory.response
        video_id = target.rsplit("=", 1)[-1]
        if video_id in self.factory.failures:
            raise DownloadError(f"candidate {video_id} unavailable")
        if video_id in self.factory.enrichments:
            return self.factory.enrichments[video_id]
        return next(
            item for item in self.factory.entries if item.get("id") == video_id
        )


class Factory:
    def __init__(
        self,
        entries=None,
        *,
        response=None,
        error=None,
        enrichments=None,
        failures=None,
    ):
        self.entries = list(entries or [])
        self.response = (
            {"entries": self.entries} if response is None else response
        )
        self.error = error
        self.enrichments = dict(enrichments or {})
        self.failures = set(failures or set())
        self.instances = []

    def __call__(self, options):
        instance = FakeYDL(self, options)
        self.instances.append(instance)
        return instance


@pytest.fixture(autouse=True)
def fallback_date_search(monkeypatch):
    monkeypatch.setattr(
        search_service, "NATIVE_DATE_SEARCH_SUPPORTED", False
    )


@pytest.mark.parametrize("query", ["", " ", "\t\n"])
def test_query_is_required(query) -> None:
    with pytest.raises(VideoExtractionError) as error:
        search_youtube(query, ydl_factory=Factory())
    assert error.value.code == "youtube_search_query_required"


def test_query_length_sort_and_limit_are_validated() -> None:
    with pytest.raises(VideoExtractionError) as query_error:
        search_youtube("x" * 201, ydl_factory=Factory())
    assert query_error.value.code == "youtube_search_query_invalid"

    with pytest.raises(VideoExtractionError) as sort_error:
        search_youtube("query", sort="popular", ydl_factory=Factory())
    assert sort_error.value.code == "youtube_search_invalid_sort"

    for limit in (0, 21):
        with pytest.raises(VideoExtractionError) as limit_error:
            search_youtube("query", limit=limit, ydl_factory=Factory())
        assert limit_error.value.code == "youtube_search_invalid_limit"


def test_default_relevance_preserves_extractor_order() -> None:
    factory = Factory(
        [entry("aaaaaa", view_count=1), entry("bbbbbb", view_count=999)]
    )
    result = search_youtube("  query  ", limit=2, ydl_factory=factory)
    assert result["sort"] == "relevance"
    assert result["query"] == "query"
    assert [item["video_id"] for item in result["results"]] == [
        "aaaaaa",
        "bbbbbb",
    ]
    assert [item["rank"] for item in result["results"]] == [1, 2]
    assert factory.instances[0].targets == [
        (f"ytsearch{YOUTUBE_SEARCH_CANDIDATE_LIMIT}:query", False)
    ]
    assert len(factory.instances) == 2


def test_latest_upload_sorts_known_dates_descending_and_missing_last() -> None:
    factory = Factory(
        [
            entry("aaaaaa", upload_date=None),
            entry("bbbbbb", upload_date="20240101"),
            entry("cccccc", upload_date="20260304"),
        ]
    )
    result = search_youtube(
        "query", sort="upload_date", limit=3, ydl_factory=factory
    )
    assert [item["video_id"] for item in result["results"]] == [
        "cccccc",
        "bbbbbb",
        "aaaaaa",
    ]
    assert result["results"][0]["upload_date"] == "2026-03-04"
    assert result["results"][-1]["upload_date"] is None
    assert result["native_date_order"] is False
    assert factory.instances[0].options["extract_flat"] == "in_playlist"
    assert factory.instances[1].options["extract_flat"] is False


def test_view_count_sorts_descending_with_missing_last() -> None:
    result = search_youtube(
        "query",
        sort="view_count",
        limit=3,
        ydl_factory=Factory(
            [
                entry("aaaaaa", view_count=None),
                entry("bbbbbb", view_count=20),
                entry("cccccc", view_count=80),
            ]
        ),
    )
    assert [item["view_count"] for item in result["results"]] == [
        80,
        20,
        None,
    ]


def test_duration_sorts_shortest_first_with_missing_last() -> None:
    result = search_youtube(
        "query",
        sort="duration",
        limit=3,
        ydl_factory=Factory(
            [
                entry("aaaaaa", duration=None),
                entry("bbbbbb", duration=754),
                entry("cccccc", duration=59),
            ]
        ),
    )
    assert [item["duration"] for item in result["results"]] == [
        59,
        754,
        None,
    ]


def test_limit_url_duration_format_and_candidate_pool() -> None:
    entries = [entry(f"video{i:02d}", duration=i) for i in range(35)]
    factory = Factory(entries)
    result = search_youtube(
        "query", sort="view_count", limit=2, ydl_factory=factory
    )
    assert len(result["results"]) == 2
    assert result["candidate_count"] == 35
    assert factory.instances[0].targets == [
        (f"ytsearch{YOUTUBE_SEARCH_CANDIDATE_LIMIT}:query", False)
    ]
    assert factory.instances[0].options["playlistend"] == 30
    assert factory.instances[0].options["skip_download"] is True
    assert factory.instances[0].options["writesubtitles"] is False
    assert factory.instances[0].options["writeautomaticsub"] is False
    assert factory.instances[1].options["skip_download"] is True
    assert result["results"][0]["url"].startswith(
        "https://www.youtube.com/watch?v="
    )
    assert format_duration(59) == "00:59"
    assert format_duration(754) == "12:34"
    assert format_duration(3723) == "1:02:03"


def test_empty_and_incomplete_results_are_safe() -> None:
    result = search_youtube(
        "query",
        ydl_factory=Factory(
            [
                {"id": "missing-title"},
                entry(
                    "valid01",
                    duration=None,
                    view_count=None,
                    upload_date=None,
                ),
            ]
        ),
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["duration_text"] is None
    assert result["results"][0]["view_count"] is None
    assert result["results"][0]["upload_date"] is None

    empty = search_youtube("query", ydl_factory=Factory())
    assert empty["results"] == []
    assert empty["candidate_count"] == 0


@pytest.mark.parametrize(
    "failure",
    [DownloadError("network failed"), RuntimeError("extractor failed")],
)
def test_yt_dlp_failure_maps_to_controlled_error(failure) -> None:
    with pytest.raises(VideoExtractionError) as error:
        search_youtube("query", ydl_factory=Factory(error=failure))
    assert error.value.code == "youtube_search_failed"
    assert "network failed" not in error.value.message


def test_search_never_invokes_whisper_or_background_jobs(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.whisper.transcribe_audio",
        lambda *_args, **_kwargs: pytest.fail("Whisper must not run"),
    )
    monkeypatch.setattr(
        "app.services.jobs.JOB_MANAGER.create_youtube_job",
        lambda *_args, **_kwargs: pytest.fail("A job must not be created"),
    )
    result = search_youtube(
        "query", ydl_factory=Factory([entry("video01")])
    )
    assert result["results"][0]["video_id"] == "video01"


def test_one_candidate_enrichment_failure_does_not_abort_upload_sort() -> None:
    entries = [
        entry(
            f"video{i:02d}",
            upload_date=f"2026{(i % 12) + 1:02d}{(i % 27) + 1:02d}",
        )
        for i in range(30)
    ]
    failed_id = entries[7]["id"]
    result = search_youtube(
        "Tiger",
        sort="upload_date",
        limit=10,
        ydl_factory=Factory(entries, failures={failed_id}),
    )
    assert result["candidate_count"] == 30
    assert result["enriched_count"] == 29
    assert result["skipped_count"] == 1
    assert len(result["results"]) == 10
    assert all(item["video_id"] != failed_id for item in result["results"])
    known_dates = [
        item["upload_date"]
        for item in result["results"]
        if item["upload_date"] is not None
    ]
    assert known_dates == sorted(known_dates, reverse=True)


@pytest.mark.parametrize(
    ("sort", "field", "reverse"),
    [("view_count", "view_count", True), ("duration", "duration", False)],
)
def test_candidate_failure_does_not_abort_other_metadata_sorts(
    sort, field, reverse
) -> None:
    entries = [entry(f"video{i:02d}", duration=100 + i, view_count=i) for i in range(30)]
    failed_id = entries[4]["id"]
    result = search_youtube(
        "Tiger",
        sort=sort,
        limit=10,
        ydl_factory=Factory(entries, failures={failed_id}),
    )
    assert result["candidate_count"] == 30
    assert result["enriched_count"] == 29
    assert result["skipped_count"] == 1
    values = [item[field] for item in result["results"]]
    assert values == sorted(values, reverse=reverse)


def test_successful_enrichment_with_missing_sort_fields_stays_last() -> None:
    entries = [
        entry("known01", upload_date="20260301", view_count=30, duration=30),
        entry("missing", upload_date=None, view_count=None, duration=None),
    ]
    for sort, field in (
        ("upload_date", "upload_date"),
        ("view_count", "view_count"),
        ("duration", "duration"),
    ):
        result = search_youtube(
            "Tiger", sort=sort, limit=2, ydl_factory=Factory(entries)
        )
        assert result["results"][-1]["video_id"] == "missing"
        assert result["results"][-1][field] is None
        assert result["skipped_count"] == 0


def test_all_candidate_enrichments_failing_is_controlled() -> None:
    entries = [entry("video01"), entry("video02")]
    with pytest.raises(VideoExtractionError) as error:
        search_youtube(
            "Tiger",
            sort="upload_date",
            ydl_factory=Factory(
                entries, failures={item["id"] for item in entries}
            ),
        )
    assert error.value.code == "youtube_search_failed"


def test_relevance_filters_proven_unavailable_candidates_and_preserves_order(
    caplog,
) -> None:
    entries = [entry("video01"), entry("video02"), entry("video03")]
    factory = Factory(entries, failures={"video01"})

    result = search_youtube("Tiger", limit=2, ydl_factory=factory)

    assert [item["video_id"] for item in result["results"]] == [
        "video02",
        "video03",
    ]
    assert result["candidate_count"] == 3
    assert result["enriched_count"] == 2
    assert result["skipped_count"] == 1
    assert factory.instances[1].options["skip_download"] is True
    assert "Skipping unavailable YouTube search candidate video01" in caplog.text
