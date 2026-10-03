from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.errors import VideoExtractionError


client = TestClient(app)


def audio_response(filename: str) -> dict[str, object]:
    return {
        "filename": filename,
        "language": "en",
        "type": "transcribed",
        "selection_mode": "direct",
        "segment_count": 1,
        "duration": 12.0,
        "range_start": 0.0,
        "range_end": 12.0,
        "segments": [{"start": 0.2, "end": 1.2, "text": "Hello"}],
        "vtt": "WEBVTT\n\n00:00:00.200 --> 00:00:01.200\nHello\n",
        "txt": "Hello",
        "srt": "1\n00:00:00,200 --> 00:00:01,200\nHello\n",
        "transcription_model": "large-v3",
        "transcription_device": "cuda",
        "transcription_compute_type": "int8_float32",
        "transcription_duration": 1.0,
    }


def test_audio_upload_is_safe_and_cleaned_after_success(monkeypatch) -> None:
    observed = {}

    def process(path: Path, filename: str, **kwargs):
        observed.update(
            path=path,
            parent=path.parent,
            filename=filename,
            content=path.read_bytes(),
            kwargs=kwargs,
        )
        assert path.name == "upload.mp3"
        return audio_response(filename)

    monkeypatch.setattr("app.routers.audio.process_uploaded_audio", process)
    response = client.post(
        "/api/audio/transcript",
        files={"file": ("../../private/speech.mp3", b"audio", "video/mp4")},
    )

    assert response.status_code == 200
    assert response.json()["selection_mode"] == "direct"
    assert observed["filename"] == "speech.mp3"
    assert observed["content"] == b"audio"
    assert not observed["parent"].exists()


def test_audio_upload_cleans_after_processing_failure(monkeypatch) -> None:
    observed_parent = None

    def fail(path: Path, _filename: str, **_kwargs):
        nonlocal observed_parent
        observed_parent = path.parent
        raise VideoExtractionError(422, "invalid_audio_file", "Invalid audio.")

    monkeypatch.setattr("app.routers.audio.process_uploaded_audio", fail)
    response = client.post(
        "/api/audio/transcript",
        files={"file": ("corrupt.wav", b"corrupt", "audio/wav")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_audio_file"
    assert observed_parent is not None and not observed_parent.exists()


def test_audio_endpoint_rejects_missing_and_unsupported_file() -> None:
    assert client.post("/api/audio/transcript").status_code == 422
    response = client.post(
        "/api/audio/transcript",
        files={"file": ("movie.mp4", b"media", "audio/mpeg")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_audio_format"


def test_audio_endpoint_forwards_optional_range(monkeypatch) -> None:
    observed = {}

    def process(_path, filename, **kwargs):
        observed.update(kwargs)
        response = audio_response(filename)
        response.update({"range_start": 60.0, "range_end": 120.0})
        return response

    monkeypatch.setattr("app.routers.audio.process_uploaded_audio", process)
    response = client.post(
        "/api/audio/transcript",
        files={"file": ("speech.m4a", b"audio", "audio/mp4")},
        data={"start_time": "0:1", "end_time": "0:2"},
    )
    assert response.status_code == 200
    assert observed == {"start_time": "0:1", "end_time": "0:2"}


def test_openapi_exposes_audio_file_and_optional_ranges() -> None:
    schema = client.get("/openapi.json").json()
    for path in ("/api/audio/transcript", "/api/jobs/audio"):
        operation = schema["paths"][path]["post"]
        content = operation["requestBody"]["content"]
        assert set(content) == {"multipart/form-data"}
        ref = content["multipart/form-data"]["schema"]["$ref"].split("/")[-1]
        body = schema["components"]["schemas"][ref]
        assert body["required"] == ["file"]
        assert {"file", "start_time", "end_time"}.issubset(body["properties"])
