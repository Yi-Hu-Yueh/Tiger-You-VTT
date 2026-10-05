from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


TERMINAL_JOB_STATES = {"stopped", "completed", "failed"}


class OverlayApiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SystemAudioDevice:
    id: int
    name: str
    is_default: bool
    sample_rate: int
    channels: int


class OverlayApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    @staticmethod
    def _safe_error(response: httpx.Response) -> OverlayApiError:
        code = "backend_request_failed"
        message = "Tiger-You-VTT backend request failed."
        try:
            payload = response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, dict):
                raw_code = detail.get("code")
                raw_message = detail.get("message")
                if isinstance(raw_code, str) and raw_code:
                    code = raw_code
                if isinstance(raw_message, str) and raw_message:
                    message = raw_message
        except (TypeError, ValueError):
            pass
        return OverlayApiError(code, message)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise OverlayApiError(
                "backend_unavailable",
                "請先啟動 Tiger-You-VTT Server。",
            ) from exc
        if not response.is_success:
            raise self._safe_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise OverlayApiError(
                "backend_invalid_response",
                "Tiger-You-VTT backend returned an invalid response.",
            ) from exc

    def health(self) -> bool:
        self._request("GET", "/api/system-audio/devices")
        return True

    def list_system_audio_devices(self) -> list[SystemAudioDevice]:
        payload = self._request("GET", "/api/system-audio/devices")
        devices = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(devices, list):
            raise OverlayApiError(
                "backend_invalid_response",
                "Tiger-You-VTT backend returned an invalid device list.",
            )
        try:
            return [
                SystemAudioDevice(
                    id=int(device["id"]),
                    name=str(device["name"]),
                    is_default=bool(device["is_default"]),
                    sample_rate=int(device["sample_rate"]),
                    channels=int(device["channels"]),
                )
                for device in devices
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise OverlayApiError(
                "backend_invalid_response",
                "Tiger-You-VTT backend returned an invalid device list.",
            ) from exc

    def start_system_audio_job(self, device_id: int, low_latency: bool = False) -> dict[str, Any]:
        request = {"device_id": device_id}
        if low_latency:
            request["low_latency"] = True
        payload = self._request(
            "POST", "/api/jobs/system-audio", json=request
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("job_id"), str
        ):
            raise OverlayApiError(
                "backend_invalid_response",
                "Tiger-You-VTT backend did not return a job ID.",
            )
        return payload

    def get_job(self, job_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/api/jobs/{job_id}")
        if not isinstance(payload, dict) or payload.get("job_id") != job_id:
            raise OverlayApiError(
                "backend_invalid_response",
                "Tiger-You-VTT backend returned an invalid job response.",
            )
        return payload

    def stop_job(self, job_id: str) -> dict[str, Any]:
        payload = self._request("POST", f"/api/jobs/{job_id}/stop")
        if not isinstance(payload, dict) or payload.get("job_id") != job_id:
            raise OverlayApiError(
                "backend_invalid_response",
                "Tiger-You-VTT backend returned an invalid STOP response.",
            )
        return payload

    def close(self) -> None:
        self._client.close()


class OwnedSystemAudioJob:
    def __init__(self, client: OverlayApiClient) -> None:
        self.client = client
        self.job_id: str | None = None
        self.status: str | None = None

    @property
    def active(self) -> bool:
        return self.job_id is not None and self.status not in TERMINAL_JOB_STATES

    def start(self, device_id: int, low_latency: bool = False) -> dict[str, Any]:
        if self.active:
            raise OverlayApiError(
                "overlay_job_active",
                "A desktop caption job is already active.",
            )
        payload = (self.client.start_system_audio_job(device_id, low_latency=True)
                   if low_latency else self.client.start_system_audio_job(device_id))
        self.job_id = str(payload["job_id"])
        self.status = str(payload.get("status", "queued"))
        return payload

    def poll(self) -> dict[str, Any]:
        if self.job_id is None:
            raise OverlayApiError(
                "overlay_job_missing", "No desktop caption job is active."
            )
        payload = self.client.get_job(self.job_id)
        self.status = str(payload.get("status", self.status or "queued"))
        return payload

    def stop(self) -> dict[str, Any] | None:
        if not self.active or self.job_id is None:
            return None
        payload = self.client.stop_job(self.job_id)
        self.status = str(payload.get("status", "stopping"))
        return payload

    def close(self) -> dict[str, Any] | None:
        return self.stop()
