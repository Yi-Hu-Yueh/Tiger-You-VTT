from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from app.services.errors import VideoExtractionError
from app.services.low_latency_audio import ContinuousCapture, run_low_latency


def wait(predicate, timeout=2):
    deadline = monotonic()+timeout
    while not predicate():
        assert monotonic() < deadline
        sleep(.005)


class Audio:
    def __init__(self, fail_open=False):
        self.active = False
        self.closed = self.terminated = False
        self.fail_open = fail_open

    def get_default_wasapi_loopback(self):
        return self.info()

    def info(self):
        return dict(index=10, name="Realtek [Loopback]", defaultSampleRate=8, maxInputChannels=1, isLoopbackDevice=True)

    def get_loopback_device_info_generator(self):
        yield self.info()

    def open(self, **kwargs):
        if self.fail_open:
            raise OSError("private details")
        assert kwargs["start"] is False
        self.callback = kwargs["stream_callback"]
        return self

    def start_stream(self):
        self.active = True

    def stop_stream(self):
        self.active = False

    def is_active(self):
        return self.active

    def close(self):
        self.closed = True

    def terminate(self):
        self.terminated = True

    def feed(self, status=0, frames=32):
        return self.callback(b"\x00\x10"*frames, frames, {}, status)


class Runtime:
    def __init__(self):
        self.preparing = Event()
        self.prepared = Event()
        self.prepared.set()
        self.entered = Event()
        self.finish = Event()
        self.finish.set()
        self.calls = 0
        self.fail = None
        self.active = self.maximum = 0

    @contextmanager
    def session(self, should_stop):
        yield True

    def prepare(self, stop):
        self.preparing.set()
        assert self.prepared.wait(2)
        if self.fail == 'prepare':
            raise VideoExtractionError(503, 'low_latency_model_unavailable', 'Safe preparation error')
        return {'model_ready': True}

    def transcribe(self, pcm, on_segment):
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.entered.set()
        assert self.finish.wait(2)
        if self.fail == 'infer':
            raise VideoExtractionError(502, 'low_latency_inference_failed', 'Safe inference error')
        if self.fail != 'silent':
            on_segment(dict(start=.1, end=1, text=f'window {self.calls}'))
        self.active -= 1
        return 'en'


class Harness:
    def __init__(self, runtime=None, audio=None):
        self.runtime = runtime or Runtime()
        self.audio = audio or Audio()
        self.stop = Event()
        self.segments = []
        self.updates = []
        self.capture = None
        self.result = self.error = None
        module = SimpleNamespace(paInt16=8, paInputOverflow=2, paContinue=0, paComplete=1, PyAudio=lambda: self.audio)

        def factory(device_id, stop, publish):
            self.capture = ContinuousCapture(device_id, stop, publish, module=module)
            return self.capture

        def execute():
            try:
                self.result = run_low_latency(10, self.stop, self.segments.append, self.updates.append, runtime=self.runtime, capture_factory=factory)
            except Exception as exc:
                self.error = exc
        self.thread = Thread(target=execute)
        self.thread.start()

    def ready(self):
        wait(lambda: self.audio.active)

    def finish(self):
        self.stop.set()
        self.runtime.prepared.set()
        self.runtime.finish.set()
        self.thread.join(3)
        assert not self.thread.is_alive()
        if self.capture:
            assert not self.capture.thread.is_alive()
            assert self.audio.terminated


def test_capture_opens_only_after_preparation():
    runtime = Runtime()
    runtime.prepared.clear()
    h = Harness(runtime)
    try:
        assert runtime.preparing.wait(1)
        assert h.capture is None
        assert h.updates[-1]['live_phase'] == 'preparing_model'
        assert h.updates[-1]['captured_frames'] == 0
        runtime.prepared.set()
        h.ready()
    finally:
        h.finish()


def test_stop_during_preparation_never_opens_capture():
    runtime = Runtime()
    runtime.prepared.clear()
    h = Harness(runtime)
    assert runtime.preparing.wait(1)
    h.finish()
    assert h.capture is None
    assert h.result['live_phase'] == 'stopping'


def test_producer_continues_during_single_consumer_and_fifo_timestamps():
    runtime = Runtime()
    runtime.finish.clear()
    h = Harness(runtime)
    try:
        h.ready()
        h.audio.feed()
        assert runtime.entered.wait(1)
        h.audio.feed()
        h.audio.feed()
        assert h.capture.queue.qsize() == 2
        assert h.capture.snapshot()['captured_frames'] == 96
        runtime.finish.set()
        wait(lambda: len(h.segments) == 3)
    finally:
        h.finish()
    assert [s['start'] for s in h.segments] == [.1, 4.1, 8.1]
    assert [s['text'] for s in h.segments] == ['window 1', 'window 2', 'window 3']
    assert runtime.maximum == 1
    assert h.result['processed_windows'] == h.result['enqueued_windows'] == 3
    assert h.result['dropped_windows'] == 0


def test_overrun_stops_capture_retains_active_text_and_safe_error():
    runtime = Runtime()
    runtime.finish.clear()
    h = Harness(runtime)
    try:
        h.ready()
        h.audio.feed()
        assert runtime.entered.wait(1)
        for _ in range(4):
            h.audio.feed()
        wait(lambda: h.audio.closed)
        runtime.finish.set()
        h.thread.join(2)
        assert not h.thread.is_alive()
        assert h.error.code == 'low_latency_overrun'
        assert h.segments[0]['text'] == 'window 1'
        assert h.updates[-1]['overflow_count'] == 1
        assert h.updates[-1]['dropped_windows'] == 1
        assert h.updates[-1]['unprocessed_windows_on_error'] == 3
    finally:
        h.finish()


def test_stop_with_backlog_closes_producer_before_inference_completes():
    runtime = Runtime()
    runtime.finish.clear()
    h = Harness(runtime)
    try:
        h.ready()
        h.audio.feed()
        assert runtime.entered.wait(1)
        h.audio.feed()
        h.audio.feed()
        h.stop.set()
        wait(lambda: h.audio.closed)
        assert h.thread.is_alive()
    finally:
        h.finish()
    assert len(h.segments) == 1
    assert h.result['unprocessed_windows_on_stop'] == 2
    assert h.result['dropped_windows'] == 0
    assert h.result['queue_depth'] == 0


def test_stop_idle_without_any_audio_or_callback_is_prompt():
    h = Harness()
    h.ready()
    start = monotonic()
    h.finish()
    assert monotonic()-start < .5
    assert h.result['captured_frames'] == 0


def test_empty_live_decode_is_normal_and_not_retried():
    runtime = Runtime()
    runtime.fail = 'silent'
    h = Harness(runtime)
    try:
        h.ready()
        h.audio.feed()
        wait(lambda: runtime.calls == 1)
    finally:
        h.finish()
    assert h.error is None
    assert not h.segments
    assert runtime.calls == 1


@pytest.mark.parametrize('failure', ['prepare','infer','open','overflow','disconnect'])
def test_failures_release_resources_and_allow_another_job(failure):
    runtime = Runtime()
    runtime.fail = failure
    h = Harness(runtime, Audio(fail_open=failure == 'open'))
    try:
        if failure not in ('prepare','open'):
            h.ready()
            if failure == 'disconnect':
                h.audio.active = False
            else:
                h.audio.feed(status=2 if failure == 'overflow' else 0)
        h.thread.join(2)
        assert not h.thread.is_alive()
        assert isinstance(h.error, VideoExtractionError)
        assert 'private' not in h.error.message
    finally:
        h.finish()
    next_job = Harness()
    next_job.ready()
    next_job.finish()


def test_partial_callback_frames_counted_separately_at_stop():
    h = Harness()
    h.ready()
    h.audio.feed(frames=3)
    h.finish()
    assert h.result['unprocessed_frames_on_stop'] == 3
    assert h.result['unprocessed_windows_on_stop'] == 0
    assert h.result['dropped_frames'] == 0
