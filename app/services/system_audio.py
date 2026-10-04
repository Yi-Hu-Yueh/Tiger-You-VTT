from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any
import wave

from app.config import SYSTEM_AUDIO_SETTINGS, SystemAudioSettings
from app.services.errors import VideoExtractionError


@dataclass(frozen=True)
class SystemAudioDevice:
    id: int
    name: str
    is_default: bool
    sample_rate: int
    channels: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_default": self.is_default,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


@dataclass(frozen=True)
class CapturedPCM:
    data: bytes
    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate


def _unsupported(message: str) -> VideoExtractionError:
    return VideoExtractionError(501, "system_audio_unsupported", message)


def _load_pyaudio(platform_name: str | None = None) -> Any:
    if (platform_name or os.name) != "nt":
        raise _unsupported("System-audio capture is supported only on Windows.")
    try:
        import pyaudiowpatch as pyaudio
    except (ImportError, OSError) as exc:
        raise _unsupported(
            "PyAudioWPatch is unavailable in the local Windows runtime."
        ) from exc
    return pyaudio


def _device_from_info(
    info: dict[str, Any], default_index: int | None
) -> SystemAudioDevice | None:
    try:
        device_id = int(info["index"])
        name = str(info["name"]).strip()
        sample_rate = int(round(float(info["defaultSampleRate"])))
        channels = int(info["maxInputChannels"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not info.get("isLoopbackDevice")
        or not name
        or sample_rate <= 0
        or channels <= 0
    ):
        return None
    return SystemAudioDevice(
        id=device_id,
        name=name,
        is_default=device_id == default_index,
        sample_rate=sample_rate,
        channels=channels,
    )


def _enumerate_devices(audio: Any) -> list[SystemAudioDevice]:
    try:
        default_info = audio.get_default_wasapi_loopback()
        default_index = int(default_info["index"])
    except (KeyError, OSError, TypeError, ValueError):
        default_index = None
    try:
        raw_devices = list(audio.get_loopback_device_info_generator())
    except (OSError, RuntimeError) as exc:
        raise VideoExtractionError(
            503,
            "system_audio_device_not_found",
            "No usable Windows WASAPI loopback output device was found.",
        ) from exc
    devices = [
        device
        for info in raw_devices
        if isinstance(info, dict)
        and (device := _device_from_info(info, default_index)) is not None
    ]
    if not devices:
        raise VideoExtractionError(
            503,
            "system_audio_device_not_found",
            "No usable Windows WASAPI loopback output device was found.",
        )
    return devices


def enumerate_system_audio_devices(
    *,
    pyaudio_module: Any | None = None,
    pyaudio_factory: Callable[[], Any] | None = None,
    platform_name: str | None = None,
) -> list[SystemAudioDevice]:
    module = pyaudio_module or _load_pyaudio(platform_name)
    audio = (pyaudio_factory or module.PyAudio)()
    try:
        return _enumerate_devices(audio)
    finally:
        audio.terminate()


def select_system_audio_device(
    device_id: int | None = None,
    *,
    pyaudio_module: Any | None = None,
    pyaudio_factory: Callable[[], Any] | None = None,
    platform_name: str | None = None,
) -> SystemAudioDevice:
    devices = enumerate_system_audio_devices(
        pyaudio_module=pyaudio_module,
        pyaudio_factory=pyaudio_factory,
        platform_name=platform_name,
    )
    if device_id is None:
        default = next((device for device in devices if device.is_default), None)
        if default is not None:
            return default
        raise VideoExtractionError(
            503,
            "system_audio_device_not_found",
            "The default Windows output has no usable WASAPI loopback device.",
        )
    selected = next((device for device in devices if device.id == device_id), None)
    if selected is None:
        raise VideoExtractionError(
            422,
            "system_audio_device_invalid",
            "The selected system-audio output device is unavailable.",
        )
    return selected


def write_pcm_wav(chunk: CapturedPCM, output_path: Path) -> Path:
    try:
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(chunk.channels)
            output.setsampwidth(chunk.sample_width)
            output.setframerate(chunk.sample_rate)
            output.writeframes(chunk.data)
    except (OSError, wave.Error) as exc:
        raise VideoExtractionError(
            500,
            "system_audio_capture_failed",
            "A captured system-audio chunk could not be prepared safely.",
        ) from exc
    return output_path


def pcm_is_silent(chunk: CapturedPCM, peak_threshold: int) -> bool:
    if chunk.sample_width != 2 or not chunk.data:
        return False
    samples = memoryview(chunk.data).cast("h")
    return max((abs(sample) for sample in samples), default=0) <= peak_threshold


@dataclass(frozen=True)
class CapturedSystemAudioChunk:
    pcm: CapturedPCM
    sequence: int
    capture_start: float
    capture_end: float
    captured_at_monotonic: float


@dataclass(frozen=True)
class SystemAudioCaptureMetrics:
    captured_frames: int
    captured_seconds: float
    enqueued_chunks: int
    processed_chunks: int
    queue_depth: int
    max_queue_depth: int
    dropped_chunks: int
    dropped_frames: int
    queue_overflow_count: int
    capture_start_monotonic: float | None
    max_processing_lag_seconds: float
    discarded_chunks_on_stop: int
    discarded_frames_on_stop: int
    discarded_chunks_on_failure: int
    discarded_frames_on_failure: int

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "captured_frames": self.captured_frames,
            "captured_seconds": self.captured_seconds,
            "enqueued_chunks": self.enqueued_chunks,
            "processed_chunks": self.processed_chunks,
            "queue_depth": self.queue_depth,
            "max_queue_depth": self.max_queue_depth,
            "dropped_chunks": self.dropped_chunks,
            "dropped_frames": self.dropped_frames,
            "queue_overflow_count": self.queue_overflow_count,
            "capture_start_monotonic": self.capture_start_monotonic,
            "max_processing_lag_seconds": self.max_processing_lag_seconds,
            "discarded_chunks_on_stop": self.discarded_chunks_on_stop,
            "discarded_frames_on_stop": self.discarded_frames_on_stop,
            "discarded_chunks_on_failure": self.discarded_chunks_on_failure,
            "discarded_frames_on_failure": self.discarded_frames_on_failure,
        }


class SystemAudioCapture:
    def __init__(
        self,
        device_id: int | None = None,
        *,
        settings: SystemAudioSettings = SYSTEM_AUDIO_SETTINGS,
        pyaudio_module: Any | None = None,
        pyaudio_factory: Callable[[], Any] | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.settings = settings
        self._module = pyaudio_module or _load_pyaudio(platform_name)
        self._audio = (pyaudio_factory or self._module.PyAudio)()
        self._stream: Any | None = None
        try:
            devices = _enumerate_devices(self._audio)
            if device_id is None:
                device = next(
                    (item for item in devices if item.is_default), None
                )
                if device is None:
                    raise VideoExtractionError(
                        503,
                        "system_audio_device_not_found",
                        "The default Windows output has no usable WASAPI loopback device.",
                    )
            else:
                device = next(
                    (item for item in devices if item.id == device_id), None
                )
                if device is None:
                    raise VideoExtractionError(
                        422,
                        "system_audio_device_invalid",
                        "The selected system-audio output device is unavailable.",
                    )
            self.device = device
            self.sample_width = int(
                self._audio.get_sample_size(self._module.paInt16)
            )
            self._stream = self._audio.open(
                format=self._module.paInt16,
                channels=device.channels,
                rate=device.sample_rate,
                input=True,
                input_device_index=device.id,
                frames_per_buffer=settings.frames_per_buffer,
            )
        except VideoExtractionError:
            self._audio.terminate()
            raise
        except Exception as exc:
            self._audio.terminate()
            raise VideoExtractionError(
                503,
                "system_audio_capture_failed",
                "The Windows WASAPI loopback stream could not be opened.",
            ) from exc

    def __enter__(self) -> SystemAudioCapture:
        return self

    def capture_chunk(
        self, should_stop: Callable[[], bool]
    ) -> CapturedPCM | None:
        if self._stream is None:
            raise VideoExtractionError(
                503,
                "system_audio_capture_failed",
                "The Windows WASAPI loopback stream is not open.",
            )
        target_frames = max(
            1, round(self.device.sample_rate * self.settings.chunk_seconds)
        )
        captured_frames = 0
        frames: list[bytes] = []
        try:
            while captured_frames < target_frames:
                if should_stop():
                    return None
                frame_count = min(
                    self.settings.frames_per_buffer,
                    target_frames - captured_frames,
                )
                data = self._stream.read(
                    frame_count, exception_on_overflow=False
                )
                if not isinstance(data, bytes) or not data:
                    raise OSError("WASAPI loopback returned no PCM frames")
                frames.append(data)
                captured_frames += frame_count
        except VideoExtractionError:
            raise
        except Exception as exc:
            raise VideoExtractionError(
                503,
                "system_audio_capture_failed",
                "Windows system-audio capture failed.",
            ) from exc
        return CapturedPCM(
            data=b"".join(frames),
            sample_rate=self.device.sample_rate,
            channels=self.device.channels,
            sample_width=self.sample_width,
            frame_count=captured_frames,
        )

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._audio.terminate()

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


class SystemAudioChunkProducer:
    """Continuously capture loopback audio into a bounded FIFO queue."""

    def __init__(
        self,
        device_id: int,
        *,
        should_stop: Callable[[], bool],
        capture_factory: Callable[[int], SystemAudioCapture] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._device_id = device_id
        self._external_should_stop = should_stop
        self._capture_factory = capture_factory or SystemAudioCapture
        self._clock = clock
        self._stop_event = Event()
        self._done_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._capture: SystemAudioCapture | None = None
        self._queue: Queue[CapturedSystemAudioChunk] | None = None
        self._failure: VideoExtractionError | None = None
        self._captured_frames = 0
        self._sample_rate = 0
        self._enqueued_chunks = 0
        self._processed_chunks = 0
        self._max_queue_depth = 0
        self._dropped_chunks = 0
        self._dropped_frames = 0
        self._queue_overflow_count = 0
        self._capture_start_monotonic: float | None = None
        self._max_processing_lag_seconds = 0.0
        self._discarded_chunks_on_stop = 0
        self._discarded_frames_on_stop = 0
        self._discarded_chunks_on_failure = 0
        self._discarded_frames_on_failure = 0

    @property
    def device(self) -> SystemAudioDevice:
        if self._capture is None:
            raise RuntimeError("System-audio producer has not been started.")
        return self._capture.device

    @property
    def settings(self) -> SystemAudioSettings:
        if self._capture is None:
            raise RuntimeError("System-audio producer has not been started.")
        return self._capture.settings

    @property
    def failure(self) -> VideoExtractionError | None:
        with self._lock:
            return self._failure

    @property
    def done(self) -> bool:
        return self._done_event.is_set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("System-audio producer has already been started.")
        capture = self._capture_factory(self._device_id)
        self._capture = capture
        self._sample_rate = capture.device.sample_rate
        self._queue = Queue(maxsize=capture.settings.queue_max_chunks)
        self._thread = Thread(
            target=self._run,
            name="system-audio-capture",
            daemon=False,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self) -> None:
        thread = self._thread
        if thread is None:
            return
        timeout = max(5.0, self.settings.chunk_seconds + 2.0)
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise VideoExtractionError(
                503,
                "system_audio_capture_failed",
                "System-audio capture did not stop cleanly.",
            )

    def get_next(self, timeout: float = 0.1) -> CapturedSystemAudioChunk | None:
        queue = self._require_queue()
        try:
            return queue.get(timeout=timeout)
        except Empty:
            return None

    def mark_processing_started(self, chunk: CapturedSystemAudioChunk) -> None:
        lag = max(0.0, self._clock() - chunk.captured_at_monotonic)
        with self._lock:
            self._max_processing_lag_seconds = max(
                self._max_processing_lag_seconds,
                lag,
            )

    def mark_processed(self) -> None:
        with self._lock:
            self._processed_chunks += 1

    def discard_pending(self, *, stopped: bool) -> None:
        queue = self._require_queue()
        discarded_chunks = 0
        discarded_frames = 0
        while True:
            try:
                chunk = queue.get_nowait()
            except Empty:
                break
            discarded_chunks += 1
            discarded_frames += chunk.pcm.frame_count
        with self._lock:
            if stopped:
                self._discarded_chunks_on_stop += discarded_chunks
                self._discarded_frames_on_stop += discarded_frames
            else:
                self._discarded_chunks_on_failure += discarded_chunks
                self._discarded_frames_on_failure += discarded_frames

    def metrics(self) -> SystemAudioCaptureMetrics:
        queue = self._queue
        queue_depth = queue.qsize() if queue is not None else 0
        with self._lock:
            captured_seconds = (
                self._captured_frames / self._sample_rate
                if self._sample_rate > 0
                else 0.0
            )
            return SystemAudioCaptureMetrics(
                captured_frames=self._captured_frames,
                captured_seconds=captured_seconds,
                enqueued_chunks=self._enqueued_chunks,
                processed_chunks=self._processed_chunks,
                queue_depth=queue_depth,
                max_queue_depth=self._max_queue_depth,
                dropped_chunks=self._dropped_chunks,
                dropped_frames=self._dropped_frames,
                queue_overflow_count=self._queue_overflow_count,
                capture_start_monotonic=self._capture_start_monotonic,
                max_processing_lag_seconds=self._max_processing_lag_seconds,
                discarded_chunks_on_stop=self._discarded_chunks_on_stop,
                discarded_frames_on_stop=self._discarded_frames_on_stop,
                discarded_chunks_on_failure=self._discarded_chunks_on_failure,
                discarded_frames_on_failure=self._discarded_frames_on_failure,
            )

    def _run(self) -> None:
        capture = self._capture
        queue = self._require_queue()
        if capture is None:
            return
        with self._lock:
            self._capture_start_monotonic = self._clock()
        sequence = 0
        try:
            while not self._should_stop():
                pcm = capture.capture_chunk(self._should_stop)
                if pcm is None:
                    break
                with self._lock:
                    capture_start = self._captured_frames / pcm.sample_rate
                    self._captured_frames += pcm.frame_count
                    capture_end = self._captured_frames / pcm.sample_rate
                chunk = CapturedSystemAudioChunk(
                    pcm=pcm,
                    sequence=sequence,
                    capture_start=capture_start,
                    capture_end=capture_end,
                    captured_at_monotonic=self._clock(),
                )
                sequence += 1
                full_since: float | None = None
                enqueued = False
                while not self._should_stop():
                    try:
                        queue.put(chunk, timeout=0.1)
                        enqueued = True
                        break
                    except Full:
                        if full_since is None:
                            full_since = self._clock()
                            with self._lock:
                                self._queue_overflow_count += 1
                        if self._clock() - full_since >= 1.0:
                            with self._lock:
                                self._dropped_chunks += 1
                                self._dropped_frames += pcm.frame_count
                            self._set_failure(
                                VideoExtractionError(
                                    503,
                                    "system_audio_queue_overflow",
                                    "System-audio processing could not keep up with capture.",
                                )
                            )
                            self._stop_event.set()
                            break
                if not enqueued:
                    if self.failure is None:
                        with self._lock:
                            self._discarded_chunks_on_stop += 1
                            self._discarded_frames_on_stop += pcm.frame_count
                    break
                with self._lock:
                    self._enqueued_chunks += 1
                    self._max_queue_depth = max(
                        self._max_queue_depth,
                        queue.qsize(),
                    )
        except VideoExtractionError as exc:
            self._set_failure(exc)
        except Exception:
            self._set_failure(
                VideoExtractionError(
                    503,
                    "system_audio_capture_failed",
                    "System-audio capture failed while recording.",
                )
            )
        finally:
            try:
                close = getattr(capture, "close", None)
                if callable(close):
                    close()
                else:
                    capture.__exit__(None, None, None)
            except Exception:
                self._set_failure(
                    VideoExtractionError(
                        503,
                        "system_audio_capture_failed",
                        "System-audio capture cleanup failed.",
                    )
                )
            self._done_event.set()

    def _should_stop(self) -> bool:
        return self._stop_event.is_set() or self._external_should_stop()

    def _set_failure(self, error: VideoExtractionError) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = error

    def _require_queue(self) -> Queue[CapturedSystemAudioChunk]:
        if self._queue is None:
            raise RuntimeError("System-audio producer has not been started.")
        return self._queue
