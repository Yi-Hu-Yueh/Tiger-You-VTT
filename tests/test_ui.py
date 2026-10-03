import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_serves_minimal_job_interface() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "開始取得字幕" in html
    assert "停止" in html
    assert 'id="stopButton"' in html
    assert 'id="transcriptText"' in html
    assert "/api/jobs/video" in html
    assert "/api/jobs/audio" in html
    assert "/api/jobs/youtube" in html
    assert 'value="audio"> 上傳語音檔' in html
    assert 'value="system_audio"> 電腦播放聲音' in html
    assert 'id="audioFile" type="file"' in html
    assert 'accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma,audio/*"' in html
    assert 'const selectedFile = mode === "audio" ? audioFile : videoFile' in html
    assert 'const endpoint = mode === "audio" ? "/api/jobs/audio" : "/api/jobs/video"' in html
    assert 'id="systemAudioDevice"' in html
    assert 'id="systemAudioControls" hidden' in html
    assert 'fetch("/api/system-audio/devices")' in html
    assert 'fetch("/api/jobs/system-audio"' in html
    assert "rangeControls.hidden = systemAudio" in html
    assert "startTime.disabled = systemAudio" in html
    assert "endTime.disabled = systemAudio" in html
    assert "`/api/jobs/${activeJobId}`" in html
    assert "`/api/jobs/${activeJobId}/stop`" in html
    assert "setTimeout(pollJob, 1000)" in html
    assert "停止會在目前安全的字幕片段完成後生效。" in html
    assert "擷取開始時間: 小時:分鐘" in html
    assert "擷取結束時間: 小時:分鐘" in html
    assert 'id="startTime"' in html
    assert 'id="endTime"' in html
    assert 'id="startTime" type="text" inputmode="numeric" value="0:0"' in html
    assert 'id="endTime" type="text" inputmode="numeric" value="0:10"' in html
    assert "擷取開始時間留空 = 從頭開始" in html
    assert "擷取結束時間留空 = 到影片結束" in html
    assert "兩者都留空 = 擷取全部" in html
    assert 'body.append("start_time", startTime.value)' in html
    assert 'body.append("end_time", endTime.value)' in html
    assert 'body.append("end_time_is_default", String(endTimeIsDefault))' in html
    assert "start_time: startTime.value" in html
    assert "end_time: endTime.value" in html
    assert "end_time_is_default: endTimeIsDefault" in html
    assert "分鐘:秒" not in html
    assert "執行所耗時間" in html
    assert 'id="elapsedTime">00:00:00<' in html
    assert "function formatElapsedTime(seconds)" in html
    assert "elapsedTime.textContent = formatElapsedTime(job.elapsed_seconds)" in html
    assert ".padStart(2, \"0\")" in html
    assert ".join(\":\")" in html
    assert 'const DEFAULT_END_TIME = "0:10"' in html
    assert "!endTimeEdited && endTime.value === DEFAULT_END_TIME" in html

    stop_handler = html.split(
        'stopButton.addEventListener("click"', 1
    )[1].split("downloadButtons.forEach", 1)[0]
    assert 'transcriptText.value = ""' not in stop_handler
    assert 'elapsedTime.textContent = "00:00:00"' not in stop_handler


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (7, "00:00:07"),
        (312, "00:05:12"),
        (27 * 3600, "27:00:00"),
    ],
)
def test_elapsed_display_examples_are_unbounded_hours(
    seconds: int, expected: str
) -> None:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60

    assert f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}" == expected
