import pytest
from fastapi.testclient import TestClient
from app.main import app

TOKEN = "test-only-" + "a" * 40


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_loopback_compatible(host, monkeypatch):
    monkeypatch.delenv("TIGER_MOBILE_TOKEN", raising=False)
    with TestClient(app, client=(host, 1234)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


@pytest.mark.parametrize("configured,supplied", [("", ""), (TOKEN, ""), (TOKEN, "wrong"), ("short", "short")])
def test_remote_fail_closed(configured, supplied, monkeypatch, caplog):
    monkeypatch.setenv("TIGER_MOBILE_TOKEN", configured)
    with TestClient(app, client=("192.0.2.9", 1234)) as client:
        for path in ["/health", "/api/jobs/missing", "/docs", "/openapi.json", "/"]:
            response = client.get(path, headers={"X-Tiger-Mobile-Token": supplied})
            assert response.status_code == 401
            assert TOKEN not in response.text
    assert TOKEN not in caplog.text


def test_remote_valid_and_overlay_stays_local(monkeypatch, caplog):
    monkeypatch.setenv("TIGER_MOBILE_TOKEN", TOKEN)
    with TestClient(app, client=("192.0.2.9", 1234), headers={"X-Tiger-Mobile-Token": TOKEN}) as client:
        response = client.get("/health")
        assert response.json() == {"status": "ok"}
        assert TOKEN not in response.text
        assert client.get("/api/jobs/missing").status_code == 404
        assert client.post("/api/overlay/start").status_code == 403
        assert client.get("/api/overlay/status").status_code == 403
    assert TOKEN not in caplog.text


def test_forwarded_query_and_duplicate_headers_do_not_authenticate(monkeypatch):
    monkeypatch.setenv("TIGER_MOBILE_TOKEN", TOKEN)
    with TestClient(app, client=("192.0.2.9", 1234)) as client:
        assert client.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401
        assert client.get("/health", params={"token": TOKEN}).status_code == 401
        assert client.get("/health", headers=[("X-Tiger-Mobile-Token", TOKEN), ("X-Tiger-Mobile-Token", TOKEN)]).status_code == 401
