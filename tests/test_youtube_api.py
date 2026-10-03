from fastapi.testclient import TestClient

from app.main import app
from app.services.youtube import VideoExtractionError


client = TestClient(app)


def normalized_info() -> dict[str, object]:
    return {
        "video_id": "abc123",
        "title": "Example video",
        "channel": "Example channel",
        "uploader": "Example uploader",
        "duration": 42,
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "original_url": "https://youtu.be/abc123",
        "thumbnail": "https://example.test/thumb.jpg",
        "language": "en",
        "subtitles": [
            {
                "language": "en",
                "type": "manual",
                "name": "English",
                "formats": [{"ext": "vtt", "url_available": True}],
            }
        ],
        "automatic_captions": [
            {
                "language": "es",
                "type": "auto",
                "name": "Spanish",
                "formats": [{"ext": "vtt", "url_available": True}],
            }
        ],
    }


def test_info_endpoint_returns_expected_schema(monkeypatch) -> None:
    monkeypatch.setattr("app.routers.youtube.get_video_info", lambda _url: normalized_info())

    response = client.post(
        "/api/youtube/info",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 200
    assert response.json() == normalized_info()
    assert response.json()["subtitles"][0]["type"] == "manual"
    assert response.json()["automatic_captions"][0]["type"] == "auto"


def test_info_endpoint_rejects_missing_url() -> None:
    response = client.post("/api/youtube/info", json={})

    assert response.status_code == 422


def test_info_endpoint_rejects_malformed_url() -> None:
    response = client.post("/api/youtube/info", json={"url": "not a URL"})

    assert response.status_code == 422


def test_info_endpoint_rejects_non_youtube_url() -> None:
    response = client.post(
        "/api/youtube/info", json={"url": "https://example.com/video"}
    )

    assert response.status_code == 422


def test_info_endpoint_returns_controlled_extraction_error(monkeypatch) -> None:
    def fail(_url: str) -> None:
        raise VideoExtractionError(
            502,
            "youtube_extraction_failed",
            "YouTube metadata could not be extracted.",
        )

    monkeypatch.setattr("app.routers.youtube.get_video_info", fail)

    response = client.post(
        "/api/youtube/info",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "youtube_extraction_failed",
            "message": "YouTube metadata could not be extracted.",
        }
    }


def subtitle_response() -> dict[str, object]:
    return {
        "video_id": "abc123",
        "title": "Example video",
        "language": "en-orig",
        "type": "auto",
        "selection_mode": "explicit",
        "segment_count": 1,
        "duration": 42.5,
        "segments": [{"start": 0.5, "end": 2.75, "text": "Hello."}],
        "vtt": "WEBVTT\n\n00:00:00.500 --> 00:00:02.750\nHello.\n",
        "txt": "Hello.",
        "srt": "1\n00:00:00,500 --> 00:00:02,750\nHello.\n",
    }


def test_subtitle_endpoint_returns_expected_schema_and_exact_language(monkeypatch) -> None:
    def fake_subtitle(_url: str, language: str, track_type: str) -> dict[str, object]:
        assert language == "en-orig"
        assert track_type == "auto"
        return subtitle_response()

    monkeypatch.setattr("app.routers.youtube.get_subtitle", fake_subtitle)

    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "language": "en-orig",
            "type": "auto",
        },
    )

    assert response.status_code == 200
    assert response.json() == subtitle_response()


def test_subtitle_endpoint_url_only_activates_auto_mode(monkeypatch) -> None:
    response_data = subtitle_response()
    response_data.update(
        {"language": "zh-TW", "type": "manual", "selection_mode": "auto"}
    )

    def fake_subtitle(
        _url: str, language: str | None, track_type: str | None
    ) -> dict[str, object]:
        assert language is None
        assert track_type is None
        return response_data

    monkeypatch.setattr("app.routers.youtube.get_subtitle", fake_subtitle)

    response = client.post(
        "/api/youtube/subtitle",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 200
    assert response.json()["selection_mode"] == "auto"


def test_subtitle_endpoint_forwards_optional_range(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_subtitle(_url, language, track_type, **kwargs):
        observed.update(kwargs)
        response = subtitle_response()
        response.update({"range_start": 60.0, "range_end": 120.0})
        return response

    monkeypatch.setattr("app.routers.youtube.get_subtitle", fake_subtitle)
    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "start_time": "0:1",
            "end_time": "0:2",
        },
    )

    assert response.status_code == 200
    assert observed == {"start_time": "0:1", "end_time": "0:2"}
    assert response.json()["range_start"] == 60.0
    assert response.json()["range_end"] == 120.0


def test_youtube_endpoint_returns_friendly_invalid_range_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.youtube._extract_raw_info",
        lambda _url: {
            "id": "abc123",
            "title": "Example",
            "duration": 300.0,
            "subtitles": {},
            "automatic_captions": {},
            "formats": [],
        },
    )
    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "start_time": "0:4",
            "end_time": "0:3",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_time_range"
    assert "晚於" in response.json()["detail"]["message"]


def test_subtitle_endpoint_accepts_transcribed_fallback_response(monkeypatch) -> None:
    response_data = {
        "video_id": "abc123",
        "title": "Example video",
        "language": "zh",
        "type": "transcribed",
        "selection_mode": "fallback",
        "segment_count": 1,
        "duration": 42.5,
        "segments": [{"start": 0.5, "end": 2.75, "text": "你好。"}],
        "vtt": "WEBVTT\n\n00:00:00.500 --> 00:00:02.750\n你好。\n",
        "txt": "你好。",
        "srt": "1\n00:00:00,500 --> 00:00:02,750\n你好。\n",
        "transcription_model": "large-v3",
        "transcription_device": "cpu",
        "transcription_compute_type": "int8",
        "transcription_duration": 10.5,
    }
    monkeypatch.setattr(
        "app.routers.youtube.get_subtitle", lambda *_args: response_data
    )

    response = client.post(
        "/api/youtube/subtitle",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 200
    assert response.json() == response_data


def test_subtitle_endpoint_rejects_language_without_type() -> None:
    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "language": "en",
        },
    )

    assert response.status_code == 422


def test_subtitle_endpoint_rejects_type_without_language() -> None:
    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "type": "manual",
        },
    )

    assert response.status_code == 422


def test_subtitle_endpoint_rejects_invalid_type() -> None:
    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "language": "en",
            "type": "translated",
        },
    )

    assert response.status_code == 422


def test_subtitle_endpoint_returns_controlled_service_error(monkeypatch) -> None:
    def fail(_url: str, _language: str, _track_type: str) -> None:
        raise VideoExtractionError(422, "subtitle_track_not_found", "Track not found.")

    monkeypatch.setattr("app.routers.youtube.get_subtitle", fail)

    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "language": "fr",
            "type": "manual",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "subtitle_track_not_found", "message": "Track not found."}
    }

