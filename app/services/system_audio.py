from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
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
