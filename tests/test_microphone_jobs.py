from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from app.config import MicrophoneSettings
from app.main import app
from app.services.errors import VideoExtractionError
from app.services.jobs import JobManager
from app.services.microphone import MicrophoneDevice, MicrophonePCM
from app.services.whisper import TranscriptionResult


client = TestClient(app, client=("127.0.0.1", 50000))
DEVICE = MicrophoneDevice(1, "Microphone", True, 8000, 1)
CHUNK = MicrophonePCM(b"\x00\x10" * 8000, 8000, 1, 2, 8000)
SILENT_CHUNK = MicrophonePCM(b"\x00\x00" * 8000, 8000, 1, 2, 8000)


def wait_for_status(job_id, expected, timeout=3.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in expected:
            return snapshot
        sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {expected}")


@pytest.fixture
def manager(monkeypatch):
    current = JobManager(max_workers=1)
    monkeypatch.setattr("app.routers.jobs.JOB_MANAGER", current)
    monkeypatch.setattr(
        "app.routers.jobs.select_microphone_device",
        lambda _device_id: DEVICE,
    )
    yield current
    current.shutdown(wait=True)


class CaptureBase:
    settings = MicrophoneSettings(1.0, 1024)
    device = DEVICE
    instances = []

    def __init__(self, device_id):
        assert device_id == DEVICE.id
        self.calls = 0
        self.closed = False
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


def result(segments, *, stopped=False, language="en", device="cuda"):
    return TranscriptionResult(
        segments=segments,
        language=language,
        model="large-v3",
        device=device,
        compute_type="int8_float32" if device == "cuda" else "int8",
        duration_seconds=0.25,
        stopped=stopped,
    )


def test_live_microphone_stop_retains_outputs_and_cleans(
    manager, monkeypatch
) -> None:
    segment_ready = Event()
    observed = {}

    class Capture(CaptureBase):
        instances = []

        def capture_chunk(self, _should_stop):
            self.calls += 1
            return CHUNK

    def transcribe(path: Path, on_segment, should_stop, **options):
        observed["directory"] = path.parent
        observed["options"] = options
        on_segment({"start": 0.25, "end": 0.75, "text": "Hello"})
        segment_ready.set()
        while not should_stop():
            sleep(0.005)
        return result(
            [{"start": 0.25, "end": 0.75, "text": "Hello"}],
            stopped=True,
        )

    monkeypatch.setattr("app.services.jobs.MicrophoneCapture", Capture)
    monkeypatch.setattr("app.services.jobs.transcribe_audio", transcribe)
    started = client.post(
        "/api/jobs/microphone", json={"device_id": 1}
    ).json()
    assert segment_ready.wait(1)
    running = client.get(f"/api/jobs/{started['job_id']}").json()
    assert running["status"] == "running"
    assert running["txt"] == "Hello"

    stopping = client.post(f"/api/jobs/{started['job_id']}/stop").json()
    assert stopping["status"] in {"stopping", "stopped"}
    stopped = wait_for_status(started["job_id"], {"stopped"})
    frozen_elapsed = stopped["elapsed_seconds"]
    sleep(0.02)
    frozen = client.get(f"/api/jobs/{started['job_id']}").json()

    assert stopped["source_type"] == "microphone"
    assert stopped["txt"] == "Hello"
    assert stopped["vtt"].startswith("WEBVTT")
    assert stopped["srt"].startswith("1\n")
    assert stopped["result"]["capture_device"]["id"] == 1
    assert stopped["result"]["transcription_model"] == "large-v3"
    assert stopped["result"]["transcription_device"] == "cuda"
    assert stopped["result"]["transcription_compute_type"] == "int8_float32"
    assert observed["options"] == {
        "retry_without_vad": False,
        "allow_empty": True,
    }
    assert Capture.instances[0].calls == 1
    assert Capture.instances[0].closed is True
    assert not observed["directory"].exists()
    assert frozen["elapsed_seconds"] == frozen_elapsed


def test_multiple_chunks_offset_once_and_support_cpu_fallback(
    manager, monkeypatch
) -> None:
    second_segment = Event()
    call_count = 0

    class Capture(CaptureBase):
        instances = []

        def capture_chunk(self, _should_stop):
            self.calls += 1
            return CHUNK

    def transcribe(_path, on_segment, should_stop, **options):
        nonlocal call_count
        assert options == {"retry_without_vad": False, "allow_empty": True}
        call_count += 1
        relative = (
            {"start": 0.1, "end": 0.4, "text": "A"}
            if call_count == 1
            else {"start": 0.2, "end": 0.6, "text": "B"}
        )
        on_segment(relative)
        if call_count == 2:
            second_segment.set()
            while not should_stop():
                sleep(0.005)
            return result([relative], stopped=True, device="cpu")
        return result([relative])

    monkeypatch.setattr("app.services.jobs.MicrophoneCapture", Capture)
    monkeypatch.setattr("app.services.jobs.transcribe_audio", transcribe)
    started = client.post("/api/jobs/microphone", json={}).json()
    assert second_segment.wait(1)
    client.post(f"/api/jobs/{started['job_id']}/stop")
    stopped = wait_for_status(started["job_id"], {"stopped"})

    assert stopped["segments"] == [
        {"start": 0.1, "end": 0.4, "text": "A"},
        {"start": 1.2, "end": 1.6, "text": "B"},
    ]
    assert stopped["txt"] == "A,B"
    assert "00:00:01.200" in stopped["vtt"]
    assert "00:00:01,200" in stopped["srt"]
    assert Capture.instances[0].calls == 2
    assert stopped["result"]["transcription_device"] == "cpu"
    assert stopped["result"]["transcription_compute_type"] == "int8"


def test_quiet_chunk_allows_empty_transcript_without_retry(
    manager, monkeypatch
) -> None:
    processed = Event()

    class Capture(CaptureBase):
        instances = []

        def capture_chunk(self, should_stop):
            self.calls += 1
            if self.calls == 1:
                return CHUNK
            while not should_stop():
                sleep(0.005)
            return None

    def transcribe(_path, _on_segment, _should_stop, **options):
        assert options == {"retry_without_vad": False, "allow_empty": True}
        processed.set()
        return result([], language="und")

    monkeypatch.setattr("app.services.jobs.MicrophoneCapture", Capture)
    monkeypatch.setattr("app.services.jobs.transcribe_audio", transcribe)
    started = client.post("/api/jobs/microphone", json={}).json()
    assert processed.wait(1)
    running = client.get(f"/api/jobs/{started['job_id']}").json()
    assert running["status"] == "running"
    assert running["segment_count"] == 0
    client.post(f"/api/jobs/{started['job_id']}/stop")
    stopped = wait_for_status(started["job_id"], {"stopped"})
    assert stopped["txt"] == ""
    assert "error" not in stopped


def test_digital_silence_skips_whisper_and_keeps_running(
    manager, monkeypatch
) -> None:
    second_capture = Event()

    class Capture(CaptureBase):
        instances = []

        def capture_chunk(self, should_stop):
            self.calls += 1
            if self.calls == 1:
                return SILENT_CHUNK
            second_capture.set()
            while not should_stop():
                sleep(0.005)
            return None

    monkeypatch.setattr("app.services.jobs.MicrophoneCapture", Capture)
    monkeypatch.setattr(
        "app.services.jobs.transcribe_audio",
        lambda *_args, **_kwargs: pytest.fail(
            "Whisper must not run for digital silence"
        ),
    )
    started = client.post("/api/jobs/microphone", json={}).json()
    assert second_capture.wait(1)
    running = client.get(f"/api/jobs/{started['job_id']}").json()
    assert running["status"] == "running"
    client.post(f"/api/jobs/{started['job_id']}/stop")
    stopped = wait_for_status(started["job_id"], {"stopped"})
    assert stopped["result"]["processed_chunks"] == 1
    assert stopped["txt"] == ""


def test_stop_before_first_text_closes_capture_without_empty_error(
    manager, monkeypatch
) -> None:
    capture_started = Event()

    class Capture(CaptureBase):
        instances = []

        def capture_chunk(self, should_stop):
            self.calls += 1
            capture_started.set()
            while not should_stop():
                sleep(0.005)
            return None

    monkeypatch.setattr("app.services.jobs.MicrophoneCapture", Capture)
    monkeypatch.setattr(
        "app.services.jobs.transcribe_audio",
        lambda *_args, **_kwargs: pytest.fail("Whisper must not run"),
    )
    started = client.post("/api/jobs/microphone", json={}).json()
    assert capture_started.wait(1)
    client.post(f"/api/jobs/{started['job_id']}/stop")
    stopped = wait_for_status(started["job_id"], {"stopped"})
    assert stopped["segment_count"] == 0
    assert stopped["txt"] == ""
    assert stopped.get("error") is None
    assert Capture.instances[0].closed is True


def test_capture_failure_preserves_partial_transcript(
    manager, monkeypatch
) -> None:
    class Capture(CaptureBase):
        instances = []

        def capture_chunk(self, _should_stop):
            self.calls += 1
            if self.calls == 1:
                return CHUNK
            raise VideoExtractionError(
                503, "microphone_capture_failed", "Capture failed."
            )

    def transcribe(_path, on_segment, _should_stop, **_options):
        segment = {"start": 0.1, "end": 0.5, "text": "Partial"}
        on_segment(segment)
        return result([segment])

    monkeypatch.setattr("app.services.jobs.MicrophoneCapture", Capture)
    monkeypatch.setattr("app.services.jobs.transcribe_audio", transcribe)
    started = client.post("/api/jobs/microphone", json={}).json()
    failed = wait_for_status(started["job_id"], {"failed"})
    assert failed["error"]["code"] == "microphone_capture_failed"
    assert failed["txt"] == "Partial"
    assert failed["vtt"].startswith("WEBVTT")
    assert failed["srt"].startswith("1\n")
    assert Capture.instances[0].closed is True
