import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError

from app.main import app
from app.services import youtube, youtube_auth as auth, youtube_runtime as runtime, youtube_search as search
from app.services.errors import VideoExtractionError

CHALLENGE = "Sign in to confirm you're not a bot"
SECRET = "SYNTHETIC_SECRET_NEVER_A_REAL_CREDENTIAL"
URL = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_DENO_PATH", raising=False)
    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "TIGER_DENO_PATH", tmp_path / "absent.exe")
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    monkeypatch.setattr(runtime, "_supported_version", lambda *_: True)


@pytest.fixture
def deno(tmp_path):
    executable = tmp_path / "deno.exe"
    executable.write_bytes(b"synthetic executable; never run")
    return executable


def test_explicit_override_highest_priority_and_relative_root(deno, monkeypatch, tmp_path):
    monkeypatch.setenv("YOUTUBE_DENO_PATH", "deno.exe")
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setattr(runtime.shutil, "which", lambda _: pytest.fail("Override must win"))
    assert runtime.discover_deno() == deno
    assert runtime.javascript_options() == {"js_runtimes": {"deno": {"path": str(deno)}}}


@pytest.mark.parametrize("kind", ["missing", "directory", "blank", "unsupported"])
def test_invalid_explicit_override_fails_closed(kind, deno, tmp_path, monkeypatch):
    values = {"missing": str(tmp_path / SECRET), "directory": str(tmp_path), "blank": " ", "unsupported": str(deno)}
    monkeypatch.setenv("YOUTUBE_DENO_PATH", values[kind])
    monkeypatch.setattr(runtime, "TIGER_DENO_PATH", deno)
    monkeypatch.setattr(runtime.shutil, "which", lambda _: pytest.fail("Invalid override must not fall back"))
    if kind == "unsupported":
        monkeypatch.setattr(runtime, "_supported_version", lambda *_: False)
    with pytest.raises(VideoExtractionError) as error:
        runtime.discover_deno()
    assert error.value.code == "youtube_deno_configuration_invalid"
    assert SECRET not in str(error.value) and str(tmp_path) not in str(error.value)


def test_path_wins_over_local_fallback(deno, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: str(deno))
    monkeypatch.setattr(runtime, "TIGER_DENO_PATH", tmp_path / "other.exe")
    assert runtime.discover_deno() == deno


def test_local_fallback_without_path(deno, monkeypatch):
    monkeypatch.setattr(runtime, "TIGER_DENO_PATH", deno)
    assert runtime.discover_deno() == deno


def test_directory_on_path_is_not_executable(deno, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: str(deno.parent))
    monkeypatch.setattr(runtime, "TIGER_DENO_PATH", deno)
    assert runtime.discover_deno() == deno


def test_missing_deno_and_path_unchanged(monkeypatch):
    before = os.environ.get("PATH")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", SECRET)
    assert runtime.discover_deno() is None
    assert runtime.javascript_options() == {"js_runtimes": {}}
    assert os.environ.get("PATH") == before


# Keep an unpatched reference to exercise the actual bounded version probe.
VERSION_PROBE = runtime._supported_version


@pytest.mark.parametrize("output,returncode,expected", [
    ("deno 2.3.0 (stable)", 0, True), ("deno 2.6.0", 0, True),
    ("deno 2.2.0", 0, False), ("node 23.0.0", 0, False), (SECRET, 1, False),
])
def test_version_probe_uses_bounded_no_shell_command(output, returncode, expected, monkeypatch):
    VERSION_PROBE.cache_clear()
    def run(args, **kwargs):
        assert args == ["synthetic-deno", "--version"]
        assert kwargs["timeout"] == 5 and kwargs["capture_output"]
        assert not kwargs.get("shell")
        return SimpleNamespace(stdout=output, returncode=returncode)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert VERSION_PROBE("synthetic-deno", 1, 1) is expected
    VERSION_PROBE.cache_clear()


def test_version_probe_failure_is_safe(monkeypatch):
    VERSION_PROBE.cache_clear()
    def run(*_, **__):
        raise PermissionError(SECRET)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert not VERSION_PROBE("synthetic-deno", 1, 1)
    VERSION_PROBE.cache_clear()


class FakeYDL:
    cookiejar = None
    def __init__(self, options): self.options = options
    def __enter__(self): return self
    def __exit__(self, *_): pass


@pytest.mark.parametrize("cookie_retry", [False, True])
def test_anonymous_first_and_one_cookie_retry_share_deno(deno, monkeypatch, cookie_retry, tmp_path):
    monkeypatch.setenv("YOUTUBE_DENO_PATH", str(deno))
    cookie = tmp_path / "synthetic-cookies.txt"
    monkeypatch.setattr(auth, "resolve_cookie_file", lambda: cookie)
    calls = []
    def operation(cookie_path):
        with auth.extractor(FakeYDL, {}, cookie_path) as ydl:
            calls.append(ydl.options)
            if cookie_retry and cookie_path is None:
                raise DownloadError(CHALLENGE)
            return "safe result"
    assert auth.with_auth_fallback(operation) == "safe result"
    assert len(calls) == (2 if cookie_retry else 1)
    assert "cookiefile" not in calls[0]
    assert all(item["js_runtimes"] == {"deno": {"path": str(deno)}} for item in calls)
    if cookie_retry: assert calls[1]["cookiefile"] == str(cookie)


@pytest.mark.parametrize("message", ["Private video", "Video has been deleted", "Video unavailable", "not available in your country", "ordinary metadata error", "Requested format is not available"])
def test_unrelated_errors_are_not_deno_errors(message):
    with pytest.raises(DownloadError, match=message):
        with auth.extractor(FakeYDL, {}):
            raise DownloadError(message)


@pytest.mark.parametrize("warn_only", [False, True])
def test_explicit_js_challenge_without_deno_is_controlled(warn_only, caplog):
    with pytest.raises(VideoExtractionError) as error:
        with auth.extractor(FakeYDL, {}) as ydl:
            ydl.options["logger"].warning("n challenge solving failed " + SECRET)
            if not warn_only: raise DownloadError("Requested format is not available " + SECRET)
    assert error.value.code == "youtube_js_runtime_required"
    assert SECRET not in str(error.value) + caplog.text


def test_missing_runtime_warning_alone_does_not_mask_private_failure():
    with pytest.raises(DownloadError, match="Private video"):
        with auth.extractor(FakeYDL, {}) as ydl:
            ydl.options["logger"].warning("No supported JavaScript runtime could be found")
            raise DownloadError("Private video")


def test_authenticated_runtime_error_is_preserved_and_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "resolve_cookie_file", lambda: tmp_path / "synthetic-cookies.txt")
    calls = []
    def operation(cookie):
        calls.append(cookie)
        with auth.extractor(FakeYDL, {}, cookie):
            raise DownloadError(CHALLENGE if cookie is None else "JavaScript challenge solving failed " + SECRET)
    with pytest.raises(VideoExtractionError) as error:
        auth.with_auth_fallback(operation)
    assert len(calls) == 2 and error.value.code == "youtube_js_runtime_required"
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("kind", ["metadata", "subtitle", "audio", "search"])
def test_all_youtube_paths_receive_runtime(kind, deno, monkeypatch, tmp_path):
    monkeypatch.setenv("YOUTUBE_DENO_PATH", str(deno))
    media = tmp_path / "media"
    media.mkdir()
    calls = []
    class YDL(FakeYDL):
        def extract_info(self, target, download=False):
            assert self.options["js_runtimes"] == {"deno": {"path": str(deno)}}
            calls.append((target, download))
            info = {"id": "abcdefghijk", "title": "Synthetic", "webpage_url": URL, "duration": 10}
            if target.startswith("ytsearch"): return {"entries": [info]}
            if download and kind == "subtitle":
                (media / "subtitle.en.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")
            elif download: (media / "audio.webm").write_bytes(b"synthetic audio")
            return info
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", YDL)
    monkeypatch.setattr(youtube, "_validate_downloaded_audio", lambda _: None)
    if kind == "metadata": youtube.get_video_info(URL)
    elif kind == "subtitle": youtube._download_selected_vtt(URL, "en", "manual", media)
    elif kind == "audio": youtube._download_audio_only(URL, media)
    else:
        assert len(search.search_youtube("test", ydl_factory=YDL)["results"]) == 1
        assert len(calls) == 2  # discovery and candidate enrichment
    assert calls


def test_invalid_config_api_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("YOUTUBE_DENO_PATH", str(tmp_path / SECRET))
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        response = client.post("/api/youtube/info", json={"url": URL})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "youtube_deno_configuration_invalid"
    assert SECRET not in response.text + caplog.text


def test_candidate_js_failure_is_not_silently_skipped():
    class YDL(FakeYDL):
        def extract_info(self, target, download=False):
            if target.startswith("ytsearch"):
                return {"entries": [{"id": "abcdefghijk", "title": "Synthetic", "webpage_url": URL, "duration": 10}]}
            raise DownloadError("No supported JavaScript runtime could be found " + SECRET)
    with pytest.raises(VideoExtractionError) as error:
        search.search_youtube("test", ydl_factory=YDL)
    assert error.value.code == "youtube_js_runtime_required"
    assert SECRET not in str(error.value)


def test_auth_challenge_still_wins_over_js_diagnostic(monkeypatch):
    monkeypatch.setattr(auth, "resolve_cookie_file", lambda: None)
    def operation(_):
        raise DownloadError(CHALLENGE + ". No supported JavaScript runtime could be found")
    with pytest.raises(VideoExtractionError) as error:
        auth.with_auth_fallback(operation)
    assert error.value.code == "youtube_auth_required"
