"""Optional continuous WASAPI path. The stable blocking capture is untouched."""
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import monotonic

from app.config import SYSTEM_AUDIO_LOW_LATENCY_WINDOW_SECONDS as WINDOW_SECONDS
from app.config import SYSTEM_AUDIO_LOW_LATENCY_QUEUE_MAX_WINDOWS as QUEUE_CAPACITY
from app.services.errors import VideoExtractionError
from app.services.media_range import offset_clip_segment
from app.services.system_audio import CapturedPCM, _enumerate_devices, _load_pyaudio


def initial_metrics():
    return dict(low_latency=True, live_model="large-v3-turbo", live_window_seconds=WINDOW_SECONDS,
                live_overlap_seconds=0.0, live_phase="preparing_model", model_ready=False,
                transcription_model="large-v3-turbo", transcription_device="cuda",
                transcription_compute_type="int8_float32", captured_frames=0, captured_seconds=0.0,
                captured_windows=0, enqueued_windows=0, processed_windows=0, queue_depth=0,
                max_queue_depth=0, overflow_count=0, dropped_frames=0, dropped_windows=0,
                unprocessed_windows_on_stop=0, unprocessed_frames_on_stop=0,
                unprocessed_windows_on_error=0, processing_lag_seconds=0.0,
                max_processing_lag_seconds=0.0, inference_seconds=0.0, last_inference_seconds=0.0)


@dataclass(frozen=True)
class LiveWindow:
    pcm: CapturedPCM
    start: float
    captured_at: float


class ContinuousCapture:
    """PortAudio callback produces PCM; a controller closes it even when idle.

    No blocking read, file I/O, inference or JobManager lock in the callback.
    The controller publishes counters and owns all PortAudio handle cleanup.
    """
    def __init__(self, device_id, stop, publish, *, module=None):
        self.device_id = device_id
        self.stop = stop
        self.publish = publish
        self.module = module
        self.queue = Queue(maxsize=QUEUE_CAPACITY)
        self.abort = Event()
        self.ready = Event()
        self.done = Event()
        self.lock = RLock()
        self.metrics = {key: value for key, value in initial_metrics().items() if key in (
            "captured_frames", "captured_seconds", "captured_windows", "enqueued_windows",
            "max_queue_depth", "overflow_count", "dropped_frames", "dropped_windows")}
        self.error = None
        self.buffer = bytearray()
        self.device = None
        self.thread = Thread(target=self._run, name="tiger-low-latency-capture", daemon=True)

    def snapshot(self):
        with self.lock:
            return {**self.metrics, "queue_depth": self.queue.qsize()}

    def fail(self, code, message):
        with self.lock:
            if self.error is None:
                self.error = VideoExtractionError(503, code, message)
        self.abort.set()

    def _callback(self, data, frame_count, timing, status):
        del timing
        module = self.module
        if self.stop.is_set() or self.abort.is_set():
            return None, module.paComplete
        try:
            with self.lock:
                if status & module.paInputOverflow:
                    self.metrics["overflow_count"] += 1
                    # PortAudio cannot quantify missing frames. Explicitly report
                    # an unknown count rather than falsely claiming zero loss.
                    self.metrics["dropped_frames"] = None
                    self.fail("low_latency_capture_overflow", "Windows reported lost system-audio frames; completed subtitles were retained.")
                    return None, module.paComplete
                if status:
                    self.fail("low_latency_capture_failed", "Windows reported a system-audio capture error.")
                    return None, module.paComplete
                stride = self.device.channels * 2
                actual = len(data) // stride
                if actual != frame_count or len(data) % stride:
                    self.metrics["dropped_frames"] += max(0, frame_count-actual)
                    self.fail("low_latency_capture_failed", "System-audio frame continuity could not be verified.")
                    return None, module.paComplete
                self.metrics["captured_frames"] += actual
                self.metrics["captured_seconds"] = self.metrics["captured_frames"] / self.device.sample_rate
                self.buffer.extend(data)
                frames = round(self.device.sample_rate * WINDOW_SECONDS)
                size = frames * stride
                while len(self.buffer) >= size:
                    pcm = CapturedPCM(bytes(self.buffer[:size]), self.device.sample_rate, self.device.channels, 2, frames)
                    del self.buffer[:size]
                    start = self.metrics["captured_windows"] * WINDOW_SECONDS
                    self.metrics["captured_windows"] += 1
                    try:
                        self.queue.put_nowait(LiveWindow(pcm, start, monotonic()))
                    except Full:
                        self.metrics["overflow_count"] += 1
                        self.metrics["dropped_windows"] += 1
                        self.metrics["dropped_frames"] += frames
                        self.fail("low_latency_overrun", "Low-latency transcription could not keep up; capture stopped and completed subtitles were retained.")
                        return None, module.paComplete
                    self.metrics["enqueued_windows"] += 1
                    self.metrics["max_queue_depth"] = max(self.metrics["max_queue_depth"], self.queue.qsize())
            return None, module.paContinue
        except Exception:
            self.fail("low_latency_capture_failed", "Continuous system-audio capture failed.")
            return None, module.paComplete

    def _run(self):
        audio = stream = None
        try:
            self.module = self.module or _load_pyaudio()
            audio = self.module.PyAudio()
            self.device = next((d for d in _enumerate_devices(audio) if d.id == self.device_id), None)
            if self.device is None:
                raise RuntimeError("selected loopback unavailable")
            if self.stop.is_set():
                return
            stream = audio.open(format=self.module.paInt16, channels=self.device.channels,
                                rate=self.device.sample_rate, input=True, input_device_index=self.device.id,
                                frames_per_buffer=480, stream_callback=self._callback, start=False)
            if self.stop.is_set():
                return
            stream.start_stream()
            with self.lock:
                self.metrics.update(live_phase="capturing", model_ready=True,
                                    capture_started_at=datetime.now(timezone.utc).isoformat(),
                                    capture_device=self.device.as_dict())
            self.ready.set()
            while not self.stop.is_set() and not self.abort.is_set():
                self.publish(self.snapshot())
                if not stream.is_active():
                    self.fail("low_latency_capture_failed", "The system-audio capture stream stopped unexpectedly.")
                    break
                self.abort.wait(0.05)
        except Exception:
            self.fail("low_latency_capture_failed", "The continuous system-audio stream could not be opened or read.")
        finally:
            try:
                if stream is not None:
                    try:
                        if stream.is_active():
                            stream.stop_stream()
                    finally:
                        stream.close()
            except Exception:
                self.fail("low_latency_capture_failed", "The system-audio stream could not be closed cleanly.")
            finally:
                if audio is not None:
                    try:
                        audio.terminate()
                    except Exception:
                        self.fail("low_latency_capture_failed", "The system-audio device could not be released cleanly.")
                self.done.set()
                self.ready.set()

    def start(self):
        self.thread.start()

    def close(self):
        self.abort.set()
        self.thread.join()


def run_low_latency(device_id, stop, on_segment, publish, *, runtime=None, capture_factory=ContinuousCapture):
    from app.services.live_whisper import LIVE_TRANSCRIBER

    runtime = runtime or LIVE_TRANSCRIBER
    metrics = initial_metrics()
    publish(dict(metrics))
    with runtime.session(stop.is_set) as acquired:
        if not acquired:
            return {**metrics, "live_phase": "stopping"}
        metrics.update(runtime.prepare(stop.is_set))
        publish(dict(metrics))
        if stop.is_set():
            return {**metrics, "live_phase": "stopping"}
        capture = capture_factory(device_id, stop, lambda counters: publish({**metrics, **counters}))
        capture.start()
        try:
            while not stop.is_set():
                if capture.error:
                    raise capture.error
                try:
                    window = capture.queue.get(timeout=0.05)
                except Empty:
                    if capture.done.is_set():
                        if capture.error:
                            raise capture.error
                        break
                    continue
                if stop.is_set() or capture.error:
                    # Consumer has removed a window but has not started it.
                    metrics["unprocessed_windows_on_stop" if stop.is_set() else "unprocessed_windows_on_error"] += 1
                    break
                started = monotonic()

                def segment_ready(segment):
                    translated = offset_clip_segment(segment, window.start, window.start + WINDOW_SECONDS)
                    if translated is not None:
                        on_segment(translated)

                language = runtime.transcribe(window.pcm, segment_ready)
                elapsed = monotonic()-started
                lag = monotonic()-window.captured_at
                metrics.update(language=language, last_inference_seconds=elapsed, processing_lag_seconds=lag)
                metrics["processed_windows"] += 1
                metrics["inference_seconds"] += elapsed
                metrics["max_processing_lag_seconds"] = max(metrics["max_processing_lag_seconds"], lag)
                publish({**metrics, **capture.snapshot(), "live_phase": "stopping" if stop.is_set() else "capturing"})
                # A capture error during active inference must not become STOP success.
                if capture.error:
                    raise capture.error
        finally:
            capture.close()
            counters = capture.snapshot()
            for key in ("captured_frames", "captured_seconds", "captured_windows", "enqueued_windows", "queue_depth", "max_queue_depth", "overflow_count", "dropped_frames", "dropped_windows", "capture_started_at", "capture_device"):
                if key in counters:
                    metrics[key] = counters[key]
            pending = capture.queue.qsize()
            if stop.is_set():
                metrics["unprocessed_windows_on_stop"] += pending
                metrics["unprocessed_frames_on_stop"] = len(capture.buffer) // (capture.device.channels*2) if capture.device else 0
            else:
                metrics["unprocessed_windows_on_error"] += pending
            while not capture.queue.empty():
                capture.queue.get_nowait()
            capture.buffer.clear()
            metrics.update(live_phase="stopping", queue_depth=0, producer_closed=not capture.thread.is_alive())
            publish(dict(metrics))
        if capture.error:
            raise capture.error
    return metrics
