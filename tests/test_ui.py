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
    assert "/api/jobs/youtube" in html
    assert "`/api/jobs/${activeJobId}`" in html
    assert "`/api/jobs/${activeJobId}/stop`" in html
    assert "setTimeout(pollJob, 1000)" in html
    assert "停止會在目前安全的字幕片段完成後生效。" in html
    assert "擷取開始時間: 小時:分鐘" in html
    assert "擷取結束時間: 小時:分鐘" in html
    assert 'id="startTime"' in html
    assert 'id="endTime"' in html
    assert html.count('placeholder="0:03"') == 2
    assert "擷取開始時間留空 = 從頭開始" in html
    assert "擷取結束時間留空 = 到影片結束" in html
    assert "兩者都留空 = 擷取全部" in html
    assert 'body.append("start_time", startTime.value)' in html
    assert 'body.append("end_time", endTime.value)' in html
    assert "start_time: startTime.value" in html
    assert "end_time: endTime.value" in html
    assert "分鐘:秒" not in html

    stop_handler = html.split(
        'stopButton.addEventListener("click"', 1
    )[1].split("downloadButtons.forEach", 1)[0]
    assert 'transcriptText.value = ""' not in stop_handler
