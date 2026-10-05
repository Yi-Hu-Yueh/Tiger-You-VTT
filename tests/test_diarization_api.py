from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def diarized_result(*, selection_mode="fallback", **identity) -> dict:
    return {
        **identity,
        "language": "en",
        "type": "transcribed",
        "selection_mode": selection_mode,
        "segment_count": 1,
        "duration": 2.0,
        "range_start": 0.0,
        "range_end": 2.0,
        "segments": [{"start": 0.0, "end": 2.0, "text": "Hello"}],
        "txt": "Hello",
        "vtt": "WEBVTT\n\n",
        "srt": "1\n",
        "diarization_enabled": True,
        "diarization_status": "completed",
        "speaker_count": 1,
        "diarized_segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "Hello",
                "speaker": "Speaker 1",
            }
        ],
        "speaker_txt": "Speaker 1：Hello",
        "speaker_vtt": "WEBVTT\n\n",
        "speaker_srt": "1\n",
        "diarization_model": "pyannote/speaker-diarization-community-1",
        "diarization_device": "cpu",
        "diarization_duration": 1.0,
    }


def test_youtube_job_diarization_defaults_off(monkeypatch) -> None:
    observed = {}

    def create(url, start, end, end_default, enabled):
        observed.update(
            url=url,
            start=start,
            end=end,
            end_default=end_default,
            enabled=enabled,
        )
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr("app.routers.jobs.JOB_MANAGER.create_youtube_job", create)

    response = client.post(
        "/api/jobs/youtube",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 202
    assert observed["enabled"] is False


def test_youtube_job_forwards_enabled_diarization(monkeypatch) -> None:
    observed = {}

    def create(url, start, end, end_default, enabled):
        observed["enabled"] = enabled
        return {"job_id": "job-2", "status": "queued"}

    monkeypatch.setattr("app.routers.jobs.JOB_MANAGER.create_youtube_job", create)

    response = client.post(
        "/api/jobs/youtube",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "enable_diarization": True,
        },
    )

    assert response.status_code == 202
    assert observed["enabled"] is True


def test_uploaded_audio_job_forwards_enabled_diarization(monkeypatch) -> None:
    observed = {}

    def create(directory, path, filename, start, end, end_default, enabled):
        observed["enabled"] = enabled
        directory.cleanup()
        return {"job_id": "job-3", "status": "queued"}

    monkeypatch.setattr("app.routers.jobs.JOB_MANAGER.create_audio_job", create)

    response = client.post(
        "/api/jobs/audio",
        files={"file": ("speech.wav", b"audio", "audio/wav")},
        data={"enable_diarization": "true"},
    )

    assert response.status_code == 202
    assert observed["enabled"] is True


def test_uploaded_video_job_forwards_enabled_diarization(monkeypatch) -> None:
    observed = {}

    def create(directory, path, filename, start, end, end_default, enabled):
        observed["enabled"] = enabled
        directory.cleanup()
        return {"job_id": "job-4", "status": "queued"}

    monkeypatch.setattr("app.routers.jobs.JOB_MANAGER.create_video_job", create)

    response = client.post(
        "/api/jobs/video",
        files={"file": ("lesson.mp4", b"video", "video/mp4")},
        data={"enable_diarization": "true"},
    )

    assert response.status_code == 202
    assert observed["enabled"] is True


def test_live_sources_reject_diarization_option() -> None:
    system_audio = client.post(
        "/api/jobs/system-audio",
        json={"device_id": 0, "enable_diarization": True},
    )
    microphone = client.post(
        "/api/jobs/microphone",
        json={"device_id": 0, "enable_diarization": True},
    )

    assert system_audio.status_code == 422
    assert microphone.status_code == 422


def test_synchronous_youtube_endpoint_forwards_diarization(monkeypatch) -> None:
    observed = {}

    def process(_url, _language, _track_type, **kwargs):
        observed.update(kwargs)
        return diarized_result(video_id="abc123", title="Interview")

    monkeypatch.setattr("app.routers.youtube.get_subtitle", process)
    response = client.post(
        "/api/youtube/subtitle",
        json={
            "url": "https://www.youtube.com/watch?v=abc123",
            "enable_diarization": True,
        },
    )

    assert response.status_code == 200
    assert observed == {"enable_diarization": True}
    assert response.json()["speaker_count"] == 1


def test_synchronous_audio_endpoint_forwards_diarization(monkeypatch) -> None:
    observed = {}

    def process(_path, filename, **kwargs):
        observed.update(kwargs)
        return diarized_result(filename=filename, selection_mode="direct")

    monkeypatch.setattr("app.routers.audio.process_uploaded_audio", process)
    response = client.post(
        "/api/audio/transcript",
        files={"file": ("speech.wav", b"audio", "audio/wav")},
        data={"enable_diarization": "true"},
    )

    assert response.status_code == 200
    assert observed["enable_diarization"] is True
    assert response.json()["diarization_status"] == "completed"


def test_synchronous_video_endpoint_forwards_diarization(monkeypatch) -> None:
    observed = {}

    def process(_path, filename, _directory, **kwargs):
        observed.update(kwargs)
        return diarized_result(filename=filename)

    monkeypatch.setattr("app.routers.video.process_uploaded_video", process)
    response = client.post(
        "/api/video/subtitle",
        files={"file": ("interview.mp4", b"video", "video/mp4")},
        data={"enable_diarization": "true"},
    )

    assert response.status_code == 200
    assert observed["enable_diarization"] is True
    assert response.json()["speaker_txt"] == "Speaker 1：Hello"
