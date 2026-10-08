import logging
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
import yt_dlp
from yt_dlp.utils import DownloadError

from app.main import app
from app.services import youtube, youtube_auth as auth, youtube_search as search
from app.services.errors import VideoExtractionError

URL = "https://www.youtube.com/watch?v=abcdefghijk"
CHALLENGE = "[youtube] Sign in to confirm you’re not a bot"
SECRET = "SYNTHETIC_COOKIE_VALUE_NOT_A_REAL_SESSION"


@pytest.fixture(autouse=True)
def isolated_cookie_config(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_COOKIES_FILE", raising=False)
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)


@pytest.fixture
def cookies(tmp_path):
    path = tmp_path / ".runtime" / "youtube_cookies.txt"
    path.parent.mkdir()
    path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tsynthetic\t" + SECRET + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("message", [
    CHALLENGE, "Sign in to confirm you're not a bot", "SIGN IN TO CONFIRM YOU'RE NOT A BOT",
    "Sign in to confirm your age", "Login required", "LOGIN_REQUIRED",
    "This video is only available for registered users", "You must log in to watch this video",
    "Video unavailable. Sign in to confirm you’re not a bot",
])
def test_explicit_auth_classifier(message):
    assert auth.is_auth_challenge(DownloadError(message))


@pytest.mark.parametrize("message", [
    "Private video. Sign in if you've been granted access to this video",
    "This video has been removed by the uploader", "This video has been deleted",
    "Video unavailable", "Video not available in your country", "Unsupported URL",
    "Ordinary metadata failure", "HTTP Error 403: Forbidden", "Timed out",
    "Use --cookies-from-browser or --cookies for authentication",
])
def test_unrelated_errors_not_auth(message):
    assert not auth.is_auth_challenge(DownloadError(message))


def test_non_yt_dlp_error_not_auth():
    assert not auth.is_auth_challenge(RuntimeError(CHALLENGE))


def test_anonymous_success_never_checks_cookies(monkeypatch):
    monkeypatch.setattr(auth, "resolve_cookie_file", lambda: pytest.fail("Anonymous fast path must not read cookie file"))
    attempts = []
    assert auth.with_auth_fallback(lambda path: attempts.append(path) or "ok") == "ok"
    assert attempts == [None]


def test_cookie_resolution_relative_override_and_missing(cookies, tmp_path, monkeypatch):
    assert auth.resolve_cookie_file() == cookies
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", ".runtime/youtube_cookies.txt")
    assert auth.resolve_cookie_file() == cookies
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookies))
    assert auth.resolve_cookie_file() == cookies
    for value in ["missing.txt", str(cookies.parent), ""]:
        monkeypatch.setenv("YOUTUBE_COOKIES_FILE", value)
        assert auth.resolve_cookie_file() is None


def test_unreadable_file_is_unavailable(cookies, monkeypatch):
    def denied(*_args, **_kwargs):
        raise PermissionError("local path diagnostic")
    monkeypatch.setattr(Path, "open", denied)
    assert auth.resolve_cookie_file() is None


@pytest.mark.parametrize("retry_error,expected", [(None, "success"), (CHALLENGE, "youtube_auth_required"), ("private video " + SECRET, "download_error")])
def test_exactly_one_retry(cookies, retry_error, expected, caplog):
    attempts = []
    def operation(path):
        attempts.append(path)
        if path is None:
            raise DownloadError(CHALLENGE)
        if retry_error:
            raise DownloadError(retry_error)
        return "success"
    if expected == "success":
        assert auth.with_auth_fallback(operation) == expected
    else:
        try:
            auth.with_auth_fallback(operation)
        except (DownloadError, VideoExtractionError) as exc:
            logging.getLogger(__name__).exception("Controlled failure")
            assert (exc.code if isinstance(exc, VideoExtractionError) else "download_error") == expected
        else:
            pytest.fail("Expected error")
    assert attempts == [None, cookies]
    assert SECRET not in caplog.text
    assert str(cookies) not in caplog.text


def test_no_cookie_auth_error_is_sanitized(caplog):
    def operation(path):
        assert path is None
        raise DownloadError(CHALLENGE + SECRET)
    with pytest.raises(VideoExtractionError) as error:
        auth.with_auth_fallback(operation)
    assert error.value.code == "youtube_auth_required"
    assert error.value.message == auth.AUTH_MESSAGE
    assert SECRET not in str(error.value)


class Factory:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []
        self.options = []

    def __call__(self, options):
        self.options.append(options)
        factory = self
        class Fake:
            cookiejar = None
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def close(self): pass
            def extract_info(self, target, download=False):
                factory.calls.append((target, download, options.get("cookiefile")))
                return factory.callback(options, target, download)
        return Fake()


def video(video_id="abcdefghijk"):
    return {"id": video_id, "title": "Synthetic video", "duration": 10, "webpage_url": URL,
            "subtitles": {"en": [{"ext": "vtt", "url": "https://example.invalid/sub"}]},
            "formats": [{"acodec": "opus", "vcodec": "none", "url": "https://example.invalid/audio"}]}


@pytest.mark.parametrize("kind", ["metadata", "subtitle", "audio"])
@pytest.mark.parametrize("challenged", [False, True])
def test_direct_flows_anonymous_and_cookie_retry(kind, challenged, cookies, tmp_path, monkeypatch):
    def callback(options, target, download):
        if challenged and not options.get("cookiefile"):
            raise DownloadError(CHALLENGE)
        if download:
            if kind == "subtitle":
                (tmp_path / "subtitle.en.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")
            else:
                (tmp_path / "audio.webm").write_bytes(b"synthetic audio")
        return video()
    factory = Factory(callback)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(youtube, "_validate_downloaded_audio", lambda path: None)
    if kind == "metadata":
        result = youtube.get_video_info(URL)
        assert SECRET not in str(result) and str(cookies) not in str(result)
    elif kind == "subtitle":
        assert "Hello" in youtube._download_selected_vtt(URL, "en", "manual", tmp_path)
    else:
        assert youtube._download_audio_only(URL, tmp_path).name == "audio.webm"
        assert all(options["format"] == "bestaudio" for options in factory.options)
    assert [call[2] for call in factory.calls] == ([None, str(cookies)] if challenged else [None])
    assert all("cookiesfrombrowser" not in options for options in factory.options)


@pytest.mark.parametrize("kind", ["metadata", "subtitle", "audio"])
def test_direct_controlled_auth_error_not_generic_failure(kind, tmp_path, monkeypatch):
    def callback(*_args): raise DownloadError(CHALLENGE)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Factory(callback))
    with pytest.raises(VideoExtractionError) as error:
        if kind == "metadata": youtube.get_video_info(URL)
        elif kind == "subtitle": youtube._download_selected_vtt(URL, "en", "manual", tmp_path)
        else: youtube._download_audio_only(URL, tmp_path)
    assert error.value.code == "youtube_auth_required"


def test_audio_authenticated_transient_error_does_not_restart_auth_cycle(cookies, tmp_path, monkeypatch):
    def callback(options, *_args):
        raise DownloadError("HTTP Error 403: " + SECRET if options.get("cookiefile") else CHALLENGE)
    factory = Factory(callback)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    with pytest.raises(VideoExtractionError) as error:
        youtube._download_audio_only(URL, tmp_path)
    assert error.value.code == "audio_download_failed"
    assert len(factory.calls) == 2


@pytest.mark.parametrize("stage", ["discovery", "candidate", "lazy_discovery"])
def test_search_auth_retry_and_candidate_tolerance(stage, cookies):
    def callback(options, target, download):
        if target.startswith("ytsearch"):
            if stage == "discovery" and not options.get("cookiefile"):
                raise DownloadError(CHALLENGE)
            if stage == "lazy_discovery" and not options.get("cookiefile"):
                def entries():
                    yield video()
                    raise DownloadError(CHALLENGE)
                return {"entries": entries()}
            return {"entries": [video("private01"), video()]}
        if "private01" in target:
            raise DownloadError("Private video. Sign in if you've been granted access")
        if stage == "candidate" and not options.get("cookiefile"):
            raise DownloadError(CHALLENGE)
        return video()
    factory = Factory(callback)
    result = search.search_youtube("test", ydl_factory=factory)
    assert result["skipped_count"] == 1
    assert len(result["results"]) == 1
    assert sum(bool(call[2]) for call in factory.calls) == 1
    assert SECRET not in str(result)


def test_candidate_auth_block_not_skipped():
    def callback(options, target, download):
        if target.startswith("ytsearch"): return {"entries": [video()]}
        raise DownloadError(CHALLENGE)
    with pytest.raises(VideoExtractionError) as error:
        search.search_youtube("test", ydl_factory=Factory(callback))
    assert error.value.code == "youtube_auth_required"


def test_late_subtitle_challenge_after_anonymous_metadata(cookies, monkeypatch):
    def callback(options, target, download):
        if download:
            if not options.get("cookiefile"): raise DownloadError(CHALLENGE)
            Path(options["outtmpl"].replace("%(ext)s", "en.vtt")).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")
        return video()
    factory = Factory(callback)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    assert youtube.get_subtitle(URL)["txt"] == "Hello"
    assert [(download, bool(cookie)) for _, download, cookie in factory.calls] == [(False, False), (True, False), (True, True)]


def test_api_failure_and_normal_response_do_not_expose_cookies(cookies, monkeypatch, caplog):
    def callback(options, *_args):
        raise DownloadError(CHALLENGE + " " + str(cookies) + " " + SECRET)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Factory(callback))
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        response = client.post("/api/youtube/info", json={"url": URL})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "youtube_auth_required"
        assert SECRET not in response.text and str(cookies) not in response.text
        assert SECRET not in client.get("/health").text
    assert SECRET not in caplog.text


def test_real_cookie_loader_malformed_row_never_echoed(cookies, capfd, caplog):
    cookies.write_text("# Netscape HTTP Cookie File\nmalformed\t" + SECRET + "\n", encoding="utf-8")
    with auth.extractor(yt_dlp.YoutubeDL, {"quiet": True, "no_warnings": True}, cookies):
        pass
    captured = capfd.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text


def test_runtime_cookie_file_gitignored():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "check-ignore", ".runtime/youtube_cookies.txt", ".runtime/mobile_api_token.txt"], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0
    assert ".runtime/youtube_cookies.txt" in result.stdout
    assert ".runtime/mobile_api_token.txt" in result.stdout


@pytest.mark.parametrize("kind,code", [("metadata", "youtube_extraction_failed"), ("subtitle", "subtitle_download_failed"), ("audio", "audio_download_failed")])
def test_retry_unrelated_error_retains_operation_error(kind, code, cookies, tmp_path, monkeypatch, caplog):
    def callback(options, *_args):
        raise DownloadError("Video unavailable " + SECRET if options.get("cookiefile") else CHALLENGE)
    factory = Factory(callback)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    try:
        if kind == "metadata": youtube.get_video_info(URL)
        elif kind == "subtitle": youtube._download_selected_vtt(URL, "en", "manual", tmp_path)
        else: youtube._download_audio_only(URL, tmp_path)
    except VideoExtractionError as error:
        logging.getLogger(__name__).exception("Controlled operation error")
        assert error.code == code
    else:
        pytest.fail("Expected controlled error")
    assert len(factory.calls) == 2
    assert SECRET not in caplog.text


def test_audio_late_auth_after_metadata_never_downloads_video(cookies, monkeypatch):
    from types import SimpleNamespace
    def callback(options, target, download):
        info = {**video(), "subtitles": {}}
        if download:
            assert options["format"] == "bestaudio"
            if not options.get("cookiefile"): raise DownloadError(CHALLENGE)
            Path(options["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"synthetic audio")
        return info
    factory = Factory(callback)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(youtube, "_validate_downloaded_audio", lambda path: None)
    monkeypatch.setattr(youtube, "_transcribe_audio", lambda *_args: SimpleNamespace(
        segments=[{"start": 0, "end": 1, "text": "Hello"}], language="en", model="test", device="cpu", compute_type="int8", duration_seconds=0.1, stopped=False))
    assert youtube.get_subtitle(URL)["txt"] == "Hello"
    assert [(download, bool(cookie)) for _, download, cookie in factory.calls] == [(False, False), (True, False), (True, True)]


def test_candidate_retry_still_challenged_aborts_search(cookies):
    def callback(options, target, download):
        if target.startswith("ytsearch"): return {"entries": [video()]}
        raise DownloadError(CHALLENGE)
    factory = Factory(callback)
    with pytest.raises(VideoExtractionError) as error:
        search.search_youtube("test", ydl_factory=factory)
    assert error.value.code == "youtube_auth_required"
    assert len(factory.calls) == 3


def test_cookie_loader_invalid_header_is_controlled(cookies, capfd, caplog):
    cookies.write_text(SECRET, encoding="utf-8")
    def operation(path):
        if path is None: raise DownloadError(CHALLENGE)
        with auth.extractor(yt_dlp.YoutubeDL, {"quiet": True}, path):
            return "unexpected"
    with pytest.raises(VideoExtractionError) as error:
        auth.with_auth_fallback(operation)
    assert error.value.code == "youtube_auth_required"
    captured = capfd.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text + str(error.value)


def test_background_job_error_is_safe_and_web_display_remains_compatible(cookies, monkeypatch, caplog):
    from time import monotonic, sleep
    from app.services.jobs import JobManager
    manager = JobManager(max_workers=1)
    monkeypatch.setattr("app.routers.jobs.JOB_MANAGER", manager)
    def callback(*_args): raise DownloadError(CHALLENGE + SECRET)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Factory(callback))
    try:
        with TestClient(app, client=("127.0.0.1", 1234)) as client:
            job_id = client.post("/api/jobs/youtube", json={"url": URL}).json()["job_id"]
            deadline = monotonic() + 5
            while monotonic() < deadline:
                response = client.get(f"/api/jobs/{job_id}")
                if response.json()["status"] == "failed": break
                sleep(0.01)
            assert response.json()["error"] == {"code": "youtube_auth_required", "message": auth.AUTH_MESSAGE}
            assert SECRET not in response.text
            page = client.get("/").text
            assert "payload.detail?.message" in page
            assert "job.error.message" in page
    finally:
        manager.shutdown(wait=True)
    assert SECRET not in caplog.text
