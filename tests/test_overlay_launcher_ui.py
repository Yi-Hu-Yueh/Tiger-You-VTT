from fastapi.testclient import TestClient

from app.main import app


def html() -> str:
    return TestClient(app).get("/").text


def test_overlay_launcher_button_exists() -> None:
    page = html()
    assert 'id="openOverlayButton"' in page
    assert "開啟桌面字幕 Overlay" in page


def test_overlay_launcher_is_inside_system_audio_controls() -> None:
    page = html()
    section_start = page.index('<div id="systemAudioControls"')
    section_end = page.index('<div id="microphoneControls"')
    button_position = page.index('id="openOverlayButton"')
    assert section_start < button_position < section_end


def test_overlay_launcher_is_not_presented_as_finite_media_feature() -> None:
    page = html()
    assert page.count('id="overlayLauncherControls"') == 1
    assert "systemAudioControls.hidden = !systemAudio" in page
    assert "beginOverlayStatusPolling();" in page


def test_overlay_launcher_button_posts_start_endpoint() -> None:
    page = html()
    handler = page.split(
        'openOverlayButton.addEventListener("click"', 1
    )[1].split('document.querySelectorAll(\'input[name="mode"]\')', 1)[0]
    assert 'fetch("/api/overlay/start", { method: "POST" })' in handler


def test_overlay_running_state_is_recognized() -> None:
    page = html()
    assert 'openOverlayButton.textContent = "Overlay 已開啟"' in page
    assert "openOverlayButton.disabled = true" in page


def test_overlay_unavailable_state_is_displayed() -> None:
    page = html()
    assert 'openOverlayButton.textContent = "Overlay 無法使用"' in page
    assert "requirements-overlay.txt" in page


def test_overlay_failed_start_uses_safe_error_message() -> None:
    page = html()
    assert 'payload.detail?.message || "桌面字幕 Overlay 啟動失敗。"' in page
    assert 'error.message || "桌面字幕 Overlay 啟動失敗。"' in page


def test_overlay_status_refresh_reenables_start_after_exit() -> None:
    page = html()
    assert 'fetch("/api/overlay/status")' in page
    assert "}, 3000);" in page
    assert 'openOverlayButton.textContent = "開啟桌面字幕 Overlay"' in page
    assert "openOverlayButton.disabled = false" in page
