from __future__ import annotations

import json

import httpx
import pytest

from app.overlay.api_client import (
    OverlayApiClient,
    OverlayApiError,
    OwnedSystemAudioJob,
)


def response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode())


def client_for(handler) -> OverlayApiClient:
    return OverlayApiClient(transport=httpx.MockTransport(handler))


def devices_payload() -> dict[str, object]:
    return {
        "devices": [
            {
                "id": 7,
                "name": "Speakers (loopback)",
                "is_default": True,
                "sample_rate": 48000,
                "channels": 2,
            }
        ]
    }


def test_health_uses_device_endpoint() -> None:
    paths: list[str] = []
    client = client_for(lambda request: paths.append(request.url.path) or response(200, devices_payload()))
    assert client.health() is True
    assert paths == ["/api/system-audio/devices"]


def test_backend_unavailable_is_safe() -> None:
    def unavailable(request):
        raise httpx.ConnectError("C:/secret/server.py", request=request)

    client = client_for(unavailable)
    with pytest.raises(OverlayApiError) as caught:
        client.health()
    assert caught.value.code == "backend_unavailable"
    assert "secret" not in caught.value.message


def test_device_enumeration_maps_contract() -> None:
    client = client_for(lambda request: response(200, devices_payload()))
    devices = client.list_system_audio_devices()
    assert [(item.id, item.name, item.is_default) for item in devices] == [
        (7, "Speakers (loopback)", True)
    ]


def test_start_job_posts_selected_device() -> None:
    bodies: list[object] = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return response(200, {"job_id": "owned", "status": "queued"})

    payload = client_for(handler).start_system_audio_job(7)
    assert payload["job_id"] == "owned"
    assert bodies == [{"device_id": 7}]


def test_poll_job_uses_exact_owned_id() -> None:
    paths: list[str] = []

    def handler(request):
        paths.append(request.url.path)
        return response(200, {"job_id": "abc-123", "status": "running", "segments": []})

    assert client_for(handler).get_job("abc-123")["status"] == "running"
    assert paths == ["/api/jobs/abc-123"]


def test_stop_job_uses_exact_owned_id() -> None:
    paths: list[str] = []

    def handler(request):
        paths.append(request.url.path)
        return response(200, {"job_id": "mine", "status": "stopping"})

    client_for(handler).stop_job("mine")
    assert paths == ["/api/jobs/mine/stop"]


def test_server_error_does_not_expose_trace_or_path() -> None:
    client = client_for(
        lambda request: response(
            500, {"traceback": "C:/private/app.py", "detail": "raw failure"}
        )
    )
    with pytest.raises(OverlayApiError) as caught:
        client.list_system_audio_devices()
    assert caught.value.message == "Tiger-You-VTT backend request failed."


def test_owned_job_poll_reaches_terminal_state() -> None:
    def handler(request):
        if request.method == "POST":
            return response(200, {"job_id": "mine", "status": "queued"})
        return response(200, {"job_id": "mine", "status": "stopped", "segments": []})

    owned = OwnedSystemAudioJob(client_for(handler))
    owned.start(7)
    owned.poll()
    assert owned.active is False


def test_close_active_job_requests_stop_once() -> None:
    stop_paths: list[str] = []

    def handler(request):
        if request.url.path == "/api/jobs/system-audio":
            return response(200, {"job_id": "mine", "status": "queued"})
        stop_paths.append(request.url.path)
        return response(200, {"job_id": "mine", "status": "stopping"})

    owned = OwnedSystemAudioJob(client_for(handler))
    owned.start(7)
    owned.close()
    assert stop_paths == ["/api/jobs/mine/stop"]


def test_close_inactive_job_never_stops_unrelated_job() -> None:
    calls: list[str] = []
    owned = OwnedSystemAudioJob(
        client_for(lambda request: calls.append(request.url.path) or response(500, {}))
    )
    assert owned.close() is None
    assert calls == []
