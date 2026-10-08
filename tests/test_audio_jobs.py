from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.errors import VideoExtractionError
from app.services.jobs import JobManager
from app.services.transcript import (
    transcript_to_srt,
    transcript_to_txt,
    transcript_to_vtt,
)


client = TestClient(app, client=("127.0.0.1", 50000))


def result_for(segments, *, filename="speech.mp3", stopped=False):
    result = {
        "filename": filename,
        "language": "en",
        "type": "transcribed",
        "selection_mode": "direct",
        "segment_count": len(segments),
        "duration": 30.0,
        "range_start": 0.0,
        "range_end": 30.0,
        "segments": segments,
        "txt": transcript_to_txt(segments),
        "vtt": transcript_to_vtt(segments),
        "srt": transcript_to_srt(segments),
        "transcription_model": "large-v3",
        "transcription_device": "cuda",
        "transcription_compute_type": "int8_float32",
        "transcription_duration": 1.0,
    }
    if stopped:
        result["_stopped"] = True
    return result


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
    yield current
    current.shutdown(wait=True)


def test_audio_job_completes_exposes_metadata_and_cleans(manager, monkeypatch) -> None:
    observed = {}
    processing_started = Event()
    release_processing = Event()

    def process(path, filename, **kwargs):
        observed.update(
            path=path,
            directory=path.parent,
            content=path.read_bytes(),
            kwargs=kwargs,
        )
        kwargs["on_range_resolved"](0.0, 30.0)
        segment = {"start": 0.0, "end": 1.0, "text": "Hello"}
        kwargs["on_segment"](segment)
        processing_started.set()
        release_processing.wait(1)
        return result_for([segment], filename=filename)

    monkeypatch.setattr("app.services.jobs.process_uploaded_audio", process)
    response = client.post(
        "/api/jobs/audio",
        files={"file": ("speech.mp3", b"audio", "audio/mpeg")},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert processing_started.wait(1)
    running = client.get(f"/api/jobs/{response.json()['job_id']}").json()
    assert running["status"] == "running"
    assert running["txt"] == "Hello"
    release_processing.set()

    snapshot = wait_for_status(response.json()["job_id"], {"completed"})
    assert snapshot["source_type"] == "audio"
    assert snapshot["txt"] == "Hello"
    assert snapshot["vtt"].startswith("WEBVTT")
    assert snapshot["srt"].startswith("1\n")
    assert snapshot["elapsed_seconds"] >= 0
    assert snapshot["result"]["selection_mode"] == "direct"
    assert observed["content"] == b"audio"
    assert not observed["directory"].exists()


def test_audio_job_forwards_range_and_default_marker(manager, monkeypatch) -> None:
    observed = {}

    def process(_path, filename, **kwargs):
        observed.update(kwargs)
        kwargs["on_range_resolved"](0.0, 120.0)
        result = result_for([], filename=filename)
        result.update({"duration": 120.0, "range_end": 120.0})
        return result

    monkeypatch.setattr("app.services.jobs.process_uploaded_audio", process)
    response = client.post(
        "/api/jobs/audio",
        files={"file": ("short.flac", b"audio", "audio/flac")},
        data={
            "start_time": "0:0",
            "end_time": "0:10",
            "end_time_is_default": "true",
        },
    )
    snapshot = wait_for_status(response.json()["job_id"], {"completed"})
    assert observed["start_time"] == "0:0"
    assert observed["end_time"] == "0:10"
    assert observed["end_time_is_default"] is True
    assert snapshot["range_end"] == 120.0


def test_audio_job_stop_preserves_partial_outputs_and_cleans(manager, monkeypatch) -> None:
    first_segment = Event()
    observed_directory = None

    def process(path, filename, **kwargs):
        nonlocal observed_directory
        observed_directory = path.parent
        kwargs["on_range_resolved"](0.0, 30.0)
        segment = {"start": 0.5, "end": 1.5, "text": "Partial"}
        kwargs["on_segment"](segment)
        first_segment.set()
        while not kwargs["should_stop"]():
            sleep(0.005)
        return result_for([segment], filename=filename, stopped=True)

    monkeypatch.setattr("app.services.jobs.process_uploaded_audio", process)
    started = client.post(
        "/api/jobs/audio",
        files={"file": ("long.wav", b"audio", "audio/wav")},
    ).json()
    assert first_segment.wait(1)

    running = client.get(f"/api/jobs/{started['job_id']}").json()
    assert running["txt"] == "Partial"
    stopping = client.post(f"/api/jobs/{started['job_id']}/stop").json()
    assert stopping["status"] in {"stopping", "stopped"}
    stopped = wait_for_status(started["job_id"], {"stopped"})
    elapsed = stopped["elapsed_seconds"]
    sleep(0.02)
    frozen = client.get(f"/api/jobs/{started['job_id']}").json()

    assert stopped["txt"] == "Partial"
    assert stopped["vtt"].startswith("WEBVTT")
    assert stopped["srt"].startswith("1\n")
    assert frozen["elapsed_seconds"] == elapsed
    assert observed_directory is not None and not observed_directory.exists()


def test_audio_job_failure_is_controlled_and_cleans(manager, monkeypatch) -> None:
    observed_directory = None

    def fail(path, _filename, **_kwargs):
        nonlocal observed_directory
        observed_directory = path.parent
        raise VideoExtractionError(422, "invalid_audio_file", "Invalid audio.")

    monkeypatch.setattr("app.services.jobs.process_uploaded_audio", fail)
    started = client.post(
        "/api/jobs/audio",
        files={"file": ("bad.ogg", b"bad", "audio/ogg")},
    ).json()
    failed = wait_for_status(started["job_id"], {"failed"})
    assert failed["error"]["code"] == "invalid_audio_file"
    assert observed_directory is not None and not observed_directory.exists()
