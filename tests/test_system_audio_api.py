from fastapi.testclient import TestClient

from app.main import app
from app.services.errors import VideoExtractionError
from app.services.system_audio import SystemAudioDevice


client = TestClient(app, client=("127.0.0.1", 50000))


def device(device_id=5, *, default=True):
    return SystemAudioDevice(
        device_id,
        "Speakers [Loopback]",
        default,
        48000,
        2,
    )


def test_device_endpoint_returns_wasapi_loopbacks(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.system_audio.enumerate_system_audio_devices",
        lambda: [device(), device(7, default=False)],
    )
    response = client.get("/api/system-audio/devices")
    assert response.status_code == 200
    assert response.json() == {
        "devices": [
            {
                "id": 5,
                "name": "Speakers [Loopback]",
                "is_default": True,
                "sample_rate": 48000,
                "channels": 2,
            },
            {
                "id": 7,
                "name": "Speakers [Loopback]",
                "is_default": False,
                "sample_rate": 48000,
                "channels": 2,
            },
        ]
    }


def test_device_endpoint_maps_controlled_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.system_audio.enumerate_system_audio_devices",
        lambda: (_ for _ in ()).throw(
            VideoExtractionError(
                503, "system_audio_device_not_found", "No loopback."
            )
        ),
    )
    response = client.get("/api/system-audio/devices")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "system_audio_device_not_found"


def test_system_audio_job_endpoint_uses_selected_or_default_device(
    monkeypatch,
) -> None:
    selected = []
    created = []

    def select(device_id):
        selected.append(device_id)
        return device(5 if device_id is None else device_id)

    monkeypatch.setattr("app.routers.jobs.select_system_audio_device", select)
    monkeypatch.setattr(
        "app.routers.jobs.JOB_MANAGER.create_system_audio_job",
        lambda device_id: created.append(device_id)
        or {"job_id": "live", "status": "queued"},
    )
    explicit = client.post("/api/jobs/system-audio", json={"device_id": 7})
    default = client.post("/api/jobs/system-audio", json={})
    assert explicit.status_code == default.status_code == 202
    assert selected == [7, None]
    assert created == [7, 5]


def test_system_audio_job_endpoint_rejects_invalid_device(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.jobs.select_system_audio_device",
        lambda _device_id: (_ for _ in ()).throw(
            VideoExtractionError(
                422, "system_audio_device_invalid", "Invalid device."
            )
        ),
    )
    response = client.post("/api/jobs/system-audio", json={"device_id": 999})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "system_audio_device_invalid"


def test_openapi_exposes_system_audio_endpoints() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/api/system-audio/devices"]
    assert "post" in paths["/api/jobs/system-audio"]
