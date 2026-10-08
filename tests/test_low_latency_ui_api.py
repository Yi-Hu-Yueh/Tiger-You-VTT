import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.jobs import JobManager
from app.services.system_audio import SystemAudioDevice
from app.services.errors import VideoExtractionError
from app.overlay.api_client import OverlayApiClient, OwnedSystemAudioJob


def wait(predicate):
    deadline = monotonic()+3
    while not predicate():
        assert monotonic() < deadline
        sleep(.005)


@pytest.mark.parametrize('body', [{}, {'low_latency': False}])
def test_default_off_invokes_original_path_without_turbo_or_queue(monkeypatch, body):
    manager = JobManager()
    calls = []
    monkeypatch.setattr('app.routers.jobs.JOB_MANAGER', manager)
    monkeypatch.setattr('app.routers.jobs.select_system_audio_device', lambda _: SystemAudioDevice(10,'Realtek',True,48000,2))
    monkeypatch.setattr(manager, '_run_system_audio_capture', lambda *args: calls.append('old') or dict(segments=[], txt='', srt='', vtt='WEBVTT\n\n', _stopped=True))
    monkeypatch.setattr(manager, '_run_low_latency_capture', lambda *args: pytest.fail('new path invoked'))
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            job = client.post('/api/jobs/system-audio', json=body).json()['job_id']
            wait(lambda: manager.snapshot(job)['status'] == 'stopped')
            assert calls == ['old']
            assert 'low_latency' not in manager.snapshot(job)['result']
    finally:
        manager.shutdown(wait=True)


@pytest.mark.parametrize('failure', [False, True])
def test_job_metadata_phases_partial_error_and_stop(monkeypatch, failure):
    manager = JobManager()
    monkeypatch.setattr('app.routers.jobs.JOB_MANAGER', manager)
    monkeypatch.setattr('app.routers.jobs.select_system_audio_device', lambda _: SystemAudioDevice(10,'Realtek',True,48000,2))
    started = Event()
    def run(device, stop, on_segment, publish):
        publish(dict(low_latency=True, live_phase='preparing_model', captured_frames=0))
        started.set()
        assert stop.wait(2)
        on_segment(dict(start=4, end=5, text='retained'))
        publish(dict(low_latency=True, live_phase='stopping', processed_windows=1))
        if failure:
            raise VideoExtractionError(503, 'low_latency_overrun', 'Capture stopped safely.')
        return dict(low_latency=True, live_phase='stopping', processed_windows=1)
    monkeypatch.setattr('app.services.low_latency_audio.run_low_latency', run)
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            job = client.post('/api/jobs/system-audio', json={'low_latency': True}).json()['job_id']
            assert started.wait(1)
            snap = client.get(f'/api/jobs/{job}').json()
            assert snap['result']['live_phase'] == 'preparing_model'
            assert snap['result']['captured_frames'] == 0
            client.post(f'/api/jobs/{job}/stop')
            wait(lambda: manager.snapshot(job)['status'] in {'failed','stopped'})
            snap = client.get(f'/api/jobs/{job}').json()
            assert snap['txt'] == 'retained'
            assert snap['result']['processed_windows'] == 1
            assert snap['status'] == ('failed' if failure else 'stopped')
    finally:
        manager.shutdown(wait=True)


def test_schema_default_and_source_scoping():
    from app.schemas.system_audio import SystemAudioJobRequest
    from app.schemas.microphone import MicrophoneJobRequest
    assert SystemAudioJobRequest().low_latency is False
    with pytest.raises(ValueError):
        MicrophoneJobRequest(low_latency=True)


def test_web_checkbox_scope_and_safe_phase_rendering():
    source = Path('app/static/index.html').read_text(encoding='utf-8')
    control = source.split('id="systemAudioControls"', 1)[1].split('id="microphoneControls"', 1)[0]
    assert '<input id="systemAudioLowLatency" type="checkbox">' in control
    assert 'low_latency: systemAudioLowLatency.checked' in source
    assert 'preparing_model: "準備低延遲模型..."' in source
    assert 'capturing: "低延遲字幕收音中"' in source
    assert 'message.textContent = detail' in source


def test_overlay_true_request_and_default_omission():
    bodies = []
    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(202,json={'job_id':'id','status':'queued'})
    client = OverlayApiClient(transport=httpx.MockTransport(handle))
    try:
        OwnedSystemAudioJob(client).start(10, low_latency=True)
        OwnedSystemAudioJob(client).start(10)
        assert bodies == [{'device_id':10,'low_latency':True}, {'device_id':10}]
    finally:
        client.close()


def test_native_checkbox_phases_caption_state_and_stop_retention():
    from PySide6.QtWidgets import QApplication
    from app.overlay.overlay_window import CaptionOverlayWindow
    from app.overlay.control_window import OverlayControlWindow
    application = QApplication.instance() or QApplication([])
    overlay = CaptionOverlayWindow()
    controls = OverlayControlWindow(overlay, auto_connect=False)
    try:
        assert not controls.low_latency_check.isChecked()
        assert controls.low_latency_check.text() == '低延遲即時字幕'
        assert controls.poll_timer.interval() == 500
        controls.job.job_id = 'id'
        controls.job.status = 'running'
        payload = dict(status='running', segments=[], result=dict(low_latency=True, live_phase='preparing_model'))
        controls._poll_ready(payload)
        application.processEvents()
        assert controls.status_label.text() == '準備低延遲模型...'
        payload['result']['live_phase'] = 'capturing'
        payload['segments'] = [dict(start=0,end=1,text='retained')]
        controls._poll_ready(payload)
        assert controls.status_label.text() == '低延遲字幕收音中'
        before = overlay.caption_label.text()
        controls.job.status = payload['status'] = 'stopped'
        controls._poll_ready(payload)
        assert overlay.caption_label.text() == before == 'retained'
    finally:
        controls.job.status = 'stopped'
        controls.close()
        controls.client.close()


def test_native_checked_start_keeps_qt_responsive_during_blocked_request():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from app.overlay.overlay_window import CaptionOverlayWindow
    from app.overlay.control_window import OverlayControlWindow

    entered, release, timer_fired = Event(), Event(), Event()
    calls = []

    class Client:
        def start_system_audio_job(self, device_id, low_latency=False):
            calls.append((device_id, low_latency))
            entered.set()
            assert release.wait(2)
            return {'job_id': 'responsive', 'status': 'queued'}

    application = QApplication.instance() or QApplication([])
    controls = OverlayControlWindow(CaptionOverlayWindow(), Client(), auto_connect=False)
    try:
        controls.device_combo.addItem('Realtek', 10)
        controls.low_latency_check.setChecked(True)
        QTimer.singleShot(0, timer_fired.set)
        controls.start_job()
        assert entered.wait(1)
        application.processEvents()
        assert timer_fired.is_set()
        assert controls.status_label.text() == '準備低延遲模型...'
        assert calls == [(10, True)]
    finally:
        release.set()
        controls.thread_pool.waitForDone(2000)
        application.processEvents()
        controls.job.status = 'stopped'
        controls.close()
