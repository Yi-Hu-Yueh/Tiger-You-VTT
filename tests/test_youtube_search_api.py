import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.errors import VideoExtractionError


client = TestClient(app)


def response(query="query", sort="relevance", limit=10):
    return {
        "query": query,
        "sort": sort,
        "limit": limit,
        "candidate_count": 1,
        "enriched_count": 1,
        "skipped_count": 0,
        "native_date_order": False,
        "results": [
            {
                "rank": 1,
                "video_id": "video01",
                "title": "Result",
                "url": "https://www.youtube.com/watch?v=video01",
                "channel": None,
                "duration": 59,
                "duration_text": "00:59",
                "view_count": None,
                "upload_date": None,
                "thumbnail": None,
            }
        ],
    }


def test_search_endpoint_passes_query_and_defaults(monkeypatch) -> None:
    calls = []

    def search(q, sort, limit):
        calls.append((q, sort, limit))
        return response(q, sort, limit)

    monkeypatch.setattr("app.routers.youtube.search_youtube", search)
    result = client.get("/api/youtube/search", params={"q": "Agentic RAG"})
    assert result.status_code == 200
    assert calls == [("Agentic RAG", "relevance", 10)]
    assert result.json()["results"][0]["duration_text"] == "00:59"


@pytest.mark.parametrize(
    "sort", ["relevance", "upload_date", "view_count", "duration"]
)
def test_search_endpoint_supports_each_sort(monkeypatch, sort) -> None:
    monkeypatch.setattr(
        "app.routers.youtube.search_youtube",
        lambda q, requested_sort, limit: response(q, requested_sort, limit),
    )
    result = client.get(
        "/api/youtube/search",
        params={"q": "query", "sort": sort, "limit": 5},
    )
    assert result.status_code == 200
    assert result.json()["sort"] == sort
    assert result.json()["limit"] == 5


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"q": "", "sort": "relevance", "limit": 10}, "youtube_search_query_required"),
        ({"q": "query", "sort": "invalid", "limit": 10}, "youtube_search_invalid_sort"),
        ({"q": "query", "sort": "relevance", "limit": 21}, "youtube_search_invalid_limit"),
    ],
)
def test_controlled_validation_errors(monkeypatch, params, code) -> None:
    def fail(*_args):
        raise VideoExtractionError(422, code, "Controlled search error.")

    monkeypatch.setattr("app.routers.youtube.search_youtube", fail)
    result = client.get("/api/youtube/search", params=params)
    assert result.status_code == 422
    assert result.json()["detail"] == {
        "code": code,
        "message": "Controlled search error.",
    }


def test_openapi_exposes_search_response_schema() -> None:
    document = client.get("/openapi.json").json()
    operation = document["paths"]["/api/youtube/search"]["get"]
    schema = operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["$ref"].endswith("/YouTubeSearchResponse")
