from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.jobs import JobManager
from app.services.transcript import transcript_to_srt, transcript_to_txt, transcript_to_vtt


client = TestClient(app)


def result_for(
    segments: list[dict[str, object]],
    *,
    filename: str | None = None,
    stopped: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "language": "en",
        "type": "transcribed",
        "selection_mode": "fallback",
        "segment_count": len(segments),
        "duration": 3.0,
        "segments": segments,
        "vtt": transcript_to_vtt(segments),
        "txt": transcript_to_txt(segments),
        "srt": transcript_to_srt(segments),
        "transcription_model": "large-v3",
        "transcription_device": "cpu",
        "transcription_compute_type": "int8",
        "transcription_duration": 1.0,
    }
    if filename is not None:
        result["filename"] = filename
    else:
        result.update({"video_id": "abc123", "title": "Example"})
    if stopped:
        result["_stopped"] = True
    return result


def wait_for_status(
    job_id: str, expected: set[str], timeout: float = 3.0
) -> dict[str, object]:
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


def test_create_upload_job_runs_to_completion_and_cleans_media(
    manager, monkeypatch
) -> None:
    observed: dict[str, object] = {}

    def process(path, filename, working_directory, **_kwargs):
        observed["path"] = path
        observed["directory"] = working_directory
        observed["content"] = path.read_bytes()
        return result_for(
            [{"start": 0.0, "end": 1.0, "text": "A"}],
            filename=filename,
        )

    monkeypatch.setattr("app.services.jobs.process_uploaded_video", process)
    response = client.post(
        "/api/jobs/video",
        files={"file": ("lesson.mp4", b"video", "video/mp4")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    snapshot = wait_for_status(response.json()["job_id"], {"completed"})
    assert observed["content"] == b"video"
    assert snapshot["segment_count"] == 1
    assert snapshot["txt"] == "A"
    assert snapshot["result"]["filename"] == "lesson.mp4"
    assert not observed["directory"].exists()


def test_create_youtube_job_transitions_to_completed(manager, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.jobs.get_subtitle",
        lambda _url, **_kwargs: result_for(
            [{"start": 0.0, "end": 1.0, "text": "YouTube"}]
        ),
    )
    response = client.post(
        "/api/jobs/youtube",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 202
    snapshot = wait_for_status(response.json()["job_id"], {"completed"})
    assert snapshot["source_type"] == "youtube"
    assert snapshot["txt"] == "YouTube"


def test_queued_job_can_be_stopped_before_processing(manager, monkeypatch) -> None:
    first_started = Event()
    release_first = Event()
    calls = 0

    def process(path, filename, _working_directory, **_kwargs):
        nonlocal calls
        calls += 1
        first_started.set()
        release_first.wait(3)
        return result_for([], filename=filename)

    monkeypatch.setattr("app.services.jobs.process_uploaded_video", process)
    first = client.post(
        "/api/jobs/video", files={"file": ("one.mp4", b"1", "video/mp4")}
    ).json()
    assert first_started.wait(1)
    second = client.post(
        "/api/jobs/video", files={"file": ("two.mp4", b"2", "video/mp4")}
    ).json()
    with manager._lock:
        queued_path = Path(manager._jobs[second["job_id"]].temporary_directory.name)

    stopped = client.post(f"/api/jobs/{second['job_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["segment_count"] == 0
    assert stopped.json()["txt"] == ""
    assert not queued_path.exists()
    assert client.post(f"/api/jobs/{second['job_id']}/stop").json()["status"] == "stopped"
    assert calls == 1
    release_first.set()
    wait_for_status(first["job_id"], {"completed"})


def test_running_job_stops_with_partial_outputs(manager, monkeypatch) -> None:
    first_segment = Event()
    release_second = Event()
    second_segment = Event()

    def process(_path, filename, _working_directory, on_segment, should_stop):
        segments = [{"start": 0.0, "end": 1.0, "text": "A"}]
        on_segment(segments[0])
        first_segment.set()
        release_second.wait(1)
        segments.append({"start": 1.0, "end": 2.0, "text": "B"})
        on_segment(segments[1])
        second_segment.set()
        while not should_stop():
            sleep(0.005)
        return result_for(segments, filename=filename, stopped=True)

    monkeypatch.setattr("app.services.jobs.process_uploaded_video", process)
    started = client.post(
        "/api/jobs/video",
        files={"file": ("partial.mp4", b"video", "video/mp4")},
    ).json()
    assert first_segment.wait(1)
    running = wait_for_status(started["job_id"], {"running"})
    assert running["txt"] == "A"
    release_second.set()
    assert second_segment.wait(1)
    deadline = monotonic() + 1
    while monotonic() < deadline:
        current = client.get(f"/api/jobs/{started['job_id']}").json()
        if current["txt"] == "A,B":
            break
        sleep(0.01)
    assert current["txt"] == "A,B"

    stop_response = client.post(f"/api/jobs/{started['job_id']}/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] in {"stopping", "stopped"}
    stopped = wait_for_status(started["job_id"], {"stopped"})
    assert stopped["segments"] == [
        {"start": 0.0, "end": 1.0, "text": "A"},
        {"start": 1.0, "end": 2.0, "text": "B"},
    ]
    assert stopped["txt"] == "A,B"
    assert all(segment["text"] != "C" for segment in stopped["segments"])
    assert stopped["vtt"].startswith("WEBVTT")
    assert stopped["srt"].startswith("1\n")
    assert client.post(f"/api/jobs/{started['job_id']}/stop").json()["status"] == "stopped"


def test_stop_does_not_change_completed_job(manager, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.jobs.get_subtitle",
        lambda _url, **_kwargs: result_for(
            [{"start": 0.0, "end": 1.0, "text": "Done"}]
        ),
    )
    started = client.post(
        "/api/jobs/youtube",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    ).json()
    wait_for_status(started["job_id"], {"completed"})

    stopped = client.post(f"/api/jobs/{started['job_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "completed"


def test_unknown_job_is_controlled_404(manager) -> None:
    response = client.get("/api/jobs/missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"

    response = client.post("/api/jobs/missing/stop")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"
