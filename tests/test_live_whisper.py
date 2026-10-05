from types import SimpleNamespace

import pytest

from app.services.live_whisper import LiveWhisper
from app.services.system_audio import CapturedPCM
from app.services.errors import VideoExtractionError
from app.services.inference_runtime import inference_session


class Model:
    def __init__(self):
        self.model = SimpleNamespace(device='cuda', compute_type='int8_float32')
        self.options = []
        self.fail = False

    def transcribe(self, _audio, **options):
        self.options.append(options)
        def decoded():
            if self.fail:
                raise RuntimeError('private model path')
            if not options['vad_filter']:
                yield SimpleNamespace(start=0, end=1, text='calibration')
        return decoded(), SimpleNamespace(language='en')


def test_lazy_cached_model_and_two_actual_warm_decodes(monkeypatch):
    monkeypatch.setattr('app.services.live_whisper._configure_windows_cuda_runtime', lambda: None)
    loaded = []
    model = Model()
    def factory(*args, **kwargs):
        loaded.append((args, kwargs))
        return model
    runtime = LiveWhisper(factory)
    assert not loaded
    first = runtime.prepare(lambda: False)
    second = runtime.prepare(lambda: False)
    assert first['model_ready'] and second['model_cached']
    assert len(loaded) == 1
    assert loaded[0][1] == dict(device='cuda', compute_type='int8_float32', local_files_only=True)
    assert len(model.options) == 2
    assert len(first['warmup_seconds']) == 2


def test_live_empty_window_always_uses_vad_and_no_retry(monkeypatch):
    runtime = LiveWhisper()
    runtime._model = Model()
    result = runtime.transcribe(CapturedPCM(b'\x00\x00'*16000,16000,1,2,16000), lambda _: pytest.fail('empty'))
    assert result == 'en'
    assert len(runtime._model.options) == 1
    assert runtime._model.options[0]['vad_filter'] is True


@pytest.mark.parametrize('where', ['load','warmup','inference'])
def test_failed_runtime_can_retry_without_poisoning_other_cache(monkeypatch, where):
    monkeypatch.setattr('app.services.live_whisper._configure_windows_cuda_runtime', lambda: None)
    model = Model()
    model.fail = where != 'load'
    def factory(*args, **kwargs):
        if where == 'load':
            raise RuntimeError('private')
        return model
    runtime = LiveWhisper(factory)
    with pytest.raises(VideoExtractionError) as error:
        if where == 'inference':
            runtime._model = model
            runtime.transcribe(CapturedPCM(b'\x00\x00'*16,16000,1,2,16), lambda _: None)
        else:
            runtime.prepare(lambda: False)
    assert 'private' not in error.value.message
    assert runtime._model is None
    assert runtime._ready is False
    runtime._factory = lambda *a, **k: Model()
    assert runtime.prepare(lambda: False)['model_ready']


def test_runtime_ownership_switch_evicts_only_finished_owner():
    released = []
    with inference_session('test-first', lambda: released.append('first')):
        assert not released
    with inference_session('test-first', lambda: None):
        assert not released
    with inference_session('test-second', lambda: None):
        assert released == ['first']


def test_cancelled_runtime_wait_does_not_activate():
    with inference_session('cancelled', lambda: pytest.fail('must not release'), lambda: True) as acquired:
        assert acquired is False
