from fastapi.testclient import TestClient

from app.main import app
from app.services.errors import VideoExtractionError
from app.services.microphone import MicrophoneDevice


client = TestClient(app, client=("127.0.0.1", 50000))


def device(device_id=1, *, default=True):
    return MicrophoneDevice(
        device_id,
        "Microphone",
        default,
        48000,
        1,
    )


def test_device_endpoint_returns_microphones(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.microphone.enumerate_microphone_devices",
        lambda: [device(), device(3, default=False)],
    )
    response = client.get("/api/microphone/devices")
    assert response.status_code == 200
    assert response.json() == {
        "devices": [
            {
                "id": 1,
                "name": "Microphone",
                "is_default": True,
                "sample_rate": 48000,
                "channels": 1,
            },
            {
                "id": 3,
                "name": "Microphone",
                "is_default": False,
                "sample_rate": 48000,
                "channels": 1,
            },
        ]
    }


def test_device_endpoint_maps_controlled_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.microphone.enumerate_microphone_devices",
        lambda: (_ for _ in ()).throw(
            VideoExtractionError(
                503, "microphone_device_not_found", "No microphone."
            )
        ),
    )
    response = client.get("/api/microphone/devices")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "microphone_device_not_found"


def test_microphone_job_endpoint_uses_selected_or_default_device(
    monkeypatch,
) -> None:
    selected = []
    created = []

    def select(device_id):
        selected.append(device_id)
        return device(1 if device_id is None else device_id)

    monkeypatch.setattr("app.routers.jobs.select_microphone_device", select)
    monkeypatch.setattr(
        "app.routers.jobs.JOB_MANAGER.create_microphone_job",
        lambda device_id: created.append(device_id)
        or {"job_id": "microphone-live", "status": "queued"},
    )
    explicit = client.post("/api/jobs/microphone", json={"device_id": 3})
    default = client.post("/api/jobs/microphone", json={})
    assert explicit.status_code == default.status_code == 202
    assert selected == [3, None]
    assert created == [3, 1]


def test_microphone_job_endpoint_rejects_invalid_device(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.jobs.select_microphone_device",
        lambda _device_id: (_ for _ in ()).throw(
            VideoExtractionError(
                422, "microphone_device_invalid", "Invalid device."
            )
        ),
    )
    response = client.post("/api/jobs/microphone", json={"device_id": 999})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "microphone_device_invalid"


def test_openapi_exposes_microphone_endpoints() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/api/microphone/devices"]
    assert "post" in paths["/api/jobs/microphone"]
