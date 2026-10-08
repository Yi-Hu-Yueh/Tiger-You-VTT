from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.errors import VideoExtractionError
from app.services.video import MediaInfo, MediaStream


client = TestClient(app, client=("127.0.0.1", 50000))


def embedded_response(filename: str) -> dict[str, object]:
    return {
        "filename": filename,
        "language": "zh-TW",
        "type": "embedded",
        "selection_mode": "auto",
        "segment_count": 2,
        "duration": 2.0,
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "A"},
            {"start": 1.0, "end": 2.0, "text": "B"},
        ],
        "vtt": "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nA\n",
        "txt": "A,B",
        "srt": "1\n00:00:00,000 --> 00:00:01,000\nA\n",
    }


def test_multipart_upload_is_safe_and_cleaned_after_success(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_process(path: Path, filename: str, working_directory: Path):
        observed["path"] = path
        observed["parent"] = working_directory
        observed["filename"] = filename
        observed["content"] = path.read_bytes()
        assert path.name == "upload.mp4"
        assert path.parent == working_directory
        return embedded_response(filename)

    monkeypatch.setattr("app.routers.video.process_uploaded_video", fake_process)
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("../../private/lesson.mp4", b"video-bytes", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "lesson.mp4"
    assert response.json()["txt"] == "A,B"
    assert observed["filename"] == "lesson.mp4"
    assert observed["content"] == b"video-bytes"
    assert not observed["parent"].exists()


def test_missing_file_is_rejected() -> None:
    response = client.post("/api/video/subtitle")

    assert response.status_code == 422


def test_unsupported_extension_is_controlled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.video.process_uploaded_video",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not process")),
    )
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("payload.exe", b"not-video", "video/mp4")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_video_format"


def test_invalid_video_is_controlled_and_cleaned_after_failure(monkeypatch) -> None:
    observed_parent: Path | None = None

    def fail(path: Path, _filename: str, working_directory: Path):
        nonlocal observed_parent
        observed_parent = working_directory
        assert path.is_file()
        raise VideoExtractionError(422, "invalid_video_file", "Invalid video.")

    monkeypatch.setattr("app.routers.video.process_uploaded_video", fail)
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("corrupt.mp4", b"corrupt", "video/mp4")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_video_file"
    assert observed_parent is not None and not observed_parent.exists()


def test_upload_storage_failure_is_controlled(monkeypatch) -> None:
    async def fail_store(*_args):
        raise VideoExtractionError(
            500, "upload_storage_failed", "Storage failed."
        )

    monkeypatch.setattr("app.routers.video.store_upload", fail_store)
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("video.mp4", b"video", "video/mp4")},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "upload_storage_failed"


def test_openapi_exposes_required_file_and_optional_range_fields() -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/video/subtitle"]["post"]
    content = operation["requestBody"]["content"]
    assert set(content) == {"multipart/form-data"}
    request_schema = content["multipart/form-data"]["schema"]
    body_schema = schema["components"]["schemas"][request_schema["$ref"].split("/")[-1]]
    assert body_schema["required"] == ["file"]
    assert set(body_schema["properties"]) == {
        "file",
        "start_time",
        "end_time",
        "enable_diarization",
    }


def test_upload_endpoint_forwards_optional_range(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_process(path, filename, directory, **kwargs):
        observed.update(kwargs)
        response = embedded_response(filename)
        response.update({"range_start": 60.0, "range_end": 120.0})
        return response

    monkeypatch.setattr("app.routers.video.process_uploaded_video", fake_process)
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("lesson.mp4", b"video", "video/mp4")},
        data={"start_time": "0:1", "end_time": "0:2"},
    )

    assert response.status_code == 200
    assert observed == {"start_time": "0:1", "end_time": "0:2"}
    assert response.json()["range_start"] == 60.0
    assert response.json()["range_end"] == 120.0


def test_upload_endpoint_returns_friendly_invalid_time_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.video.probe_media",
        lambda _path: MediaInfo(
            300.0, (MediaStream(0, "video", "h264"),)
        ),
    )
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("lesson.mp4", b"video", "video/mp4")},
        data={"start_time": "分鐘"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_time_format"
    assert "小時:分鐘" in response.json()["detail"]["message"]
