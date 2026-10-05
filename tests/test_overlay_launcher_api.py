from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import overlay as overlay_router_module
from app.services.overlay_launcher import OverlayLauncher


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class RecordingFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess()
        self.calls.append((command, kwargs))
        self.processes.append(process)
        return process


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch) -> tuple[OverlayLauncher, RecordingFactory]:
    factory = RecordingFactory()
    instance = OverlayLauncher(
        executable="C:/safe/python.exe",
        project_root=PROJECT_ROOT,
        process_factory=factory,
        spec_finder=lambda name: object(),
    )
    monkeypatch.setattr(overlay_router_module, "overlay_launcher", instance)
    return instance, factory


def loopback_client() -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def test_loopback_request_can_query_status(launcher) -> None:
    response = loopback_client().get("/api/overlay/status")
    assert response.status_code == 200
    assert response.json() == {"available": True, "running": False}


def test_loopback_request_can_start_overlay(launcher) -> None:
    response = loopback_client().post("/api/overlay/start")
    assert response.status_code == 200
    assert response.json()["status"] == "started"


def test_non_loopback_request_is_forbidden(launcher) -> None:
    response = TestClient(app, client=("192.0.2.10", 50000)).post(
        "/api/overlay/start"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "overlay_localhost_required"


@pytest.mark.parametrize(
    ("url", "json_body"),
    [
        ("/api/overlay/start?module=evil.module", None),
        ("/api/overlay/start", {"command": "calc.exe"}),
    ],
)
def test_arbitrary_launch_parameters_are_rejected(
    launcher, url: str, json_body: object
) -> None:
    _instance, factory = launcher
    response = loopback_client().post(url, json=json_body)
    assert response.status_code == 422
    assert factory.calls == []


def test_launch_target_is_exact_fixed_module(launcher) -> None:
    _instance, factory = launcher
    loopback_client().post("/api/overlay/start")
    assert factory.calls[0][0] == [
        "C:/safe/python.exe",
        "-m",
        "app.overlay.main",
    ]
    assert factory.calls[0][1]["cwd"] == str(PROJECT_ROOT)


def test_launcher_explicitly_disables_shell(launcher) -> None:
    _instance, factory = launcher
    loopback_client().post("/api/overlay/start")
    assert factory.calls[0][1]["shell"] is False


def test_repeated_start_does_not_create_second_process(launcher) -> None:
    _instance, factory = launcher
    first = loopback_client().post("/api/overlay/start")
    second = loopback_client().post("/api/overlay/start")
    assert first.json()["status"] == "started"
    assert second.json()["status"] == "already_running"
    assert len(factory.calls) == 1


def test_start_launches_again_after_owned_process_exits(launcher) -> None:
    _instance, factory = launcher
    loopback_client().post("/api/overlay/start")
    factory.processes[0].returncode = 0
    response = loopback_client().post("/api/overlay/start")
    assert response.json()["status"] == "started"
    assert len(factory.calls) == 2


def test_core_api_import_does_not_import_pyside6() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.main; assert 'PySide6' not in sys.modules",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_unavailable_overlay_returns_controlled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = OverlayLauncher(spec_finder=lambda name: None)
    monkeypatch.setattr(overlay_router_module, "overlay_launcher", instance)
    response = loopback_client().post("/api/overlay/start")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "overlay_unavailable",
        "message": "桌面字幕 Overlay 無法使用；請安裝選用的 Overlay 套件。",
    }


def test_ipv6_loopback_can_start_and_read_status(launcher) -> None:
    client = TestClient(app, client=("::1", 50000))
    assert client.get("/api/overlay/status").status_code == 200
    assert client.post("/api/overlay/start").status_code == 200


def test_remote_status_is_denied_even_with_forwarded_header(launcher) -> None:
    client = TestClient(app, client=("192.0.2.10", 50000))
    assert client.get("/api/overlay/status", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_remote_web_origin_cannot_launch_local_process(launcher) -> None:
    _, factory = launcher
    response = loopback_client().post("/api/overlay/start", headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
    assert not factory.calls


def test_same_origin_browser_can_launch(launcher) -> None:
    response = loopback_client().post("/api/overlay/start", headers={"Origin": "http://testserver"})
    assert response.status_code == 200


def test_default_command_uses_current_interpreter() -> None:
    factory = RecordingFactory()
    instance = OverlayLauncher(process_factory=factory, spec_finder=lambda _: object())
    instance.start()
    assert factory.calls[0][0] == [sys.executable, "-m", "app.overlay.main"]


def test_concurrent_starts_create_only_one_process() -> None:
    from concurrent.futures import ThreadPoolExecutor
    factory = RecordingFactory()
    instance = OverlayLauncher(process_factory=factory, spec_finder=lambda _: object())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: instance.start(), range(8)))
    assert len(factory.calls) == 1
    assert sum(item["status"] == "started" for item in results) == 1


def test_process_spawn_failure_is_safe(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise OSError("private local path")
    instance = OverlayLauncher(process_factory=fail, spec_finder=lambda _: object())
    monkeypatch.setattr(overlay_router_module, "overlay_launcher", instance)
    response = loopback_client().post("/api/overlay/start")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "overlay_launch_failed"
    assert "private" not in response.text


def test_missing_overlay_package_is_unavailable() -> None:
    def missing(name):
        raise ModuleNotFoundError(name)
    assert OverlayLauncher(spec_finder=missing).status() == {"available": False, "running": False}
