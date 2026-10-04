from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
import wave

from app.config import MICROPHONE_SETTINGS, MicrophoneSettings
from app.services.errors import VideoExtractionError


@dataclass(frozen=True)
class MicrophoneDevice:
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
class MicrophonePCM:
    data: bytes
    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate


def _unsupported(message: str) -> VideoExtractionError:
    return VideoExtractionError(501, "microphone_unsupported", message)


def _load_pyaudio(platform_name: str | None = None) -> Any:
    if (platform_name or os.name) != "nt":
        raise _unsupported("Microphone capture is supported only on Windows.")
    try:
        import pyaudiowpatch as pyaudio
    except (ImportError, OSError) as exc:
        raise _unsupported(
            "PyAudioWPatch is unavailable in the local Windows runtime."
        ) from exc
    return pyaudio


def _default_input_index(audio: Any) -> int | None:
    try:
        info = audio.get_default_input_device_info()
        return int(info["index"])
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _device_from_info(
    info: dict[str, Any], default_index: int | None
) -> MicrophoneDevice | None:
    try:
        device_id = int(info["index"])
        name = str(info["name"]).strip()
        sample_rate = int(round(float(info["defaultSampleRate"])))
        channels = int(info["maxInputChannels"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        info.get("isLoopbackDevice")
        or not name
        or sample_rate <= 0
        or channels <= 0
    ):
        return None
    return MicrophoneDevice(
        id=device_id,
        name=name,
        is_default=device_id == default_index,
        sample_rate=sample_rate,
        channels=channels,
    )


def _enumerate_devices(audio: Any) -> list[MicrophoneDevice]:
    default_index = _default_input_index(audio)
    try:
        raw_devices = [
            audio.get_device_info_by_index(index)
            for index in range(int(audio.get_device_count()))
        ]
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise VideoExtractionError(
            503,
            "microphone_device_not_found",
            "No usable Windows microphone input device was found.",
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
            "microphone_device_not_found",
            "No usable Windows microphone input device was found.",
        )
    return devices


def enumerate_microphone_devices(
    *,
    pyaudio_module: Any | None = None,
    pyaudio_factory: Callable[[], Any] | None = None,
    platform_name: str | None = None,
) -> list[MicrophoneDevice]:
    module = pyaudio_module or _load_pyaudio(platform_name)
    audio = (pyaudio_factory or module.PyAudio)()
    try:
        return _enumerate_devices(audio)
    finally:
        audio.terminate()


def select_microphone_device(
    device_id: int | None = None,
    *,
    pyaudio_module: Any | None = None,
    pyaudio_factory: Callable[[], Any] | None = None,
    platform_name: str | None = None,
) -> MicrophoneDevice:
    devices = enumerate_microphone_devices(
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
            "microphone_device_not_found",
            "The default Windows microphone input is unavailable.",
        )
    selected = next((device for device in devices if device.id == device_id), None)
    if selected is None:
        raise VideoExtractionError(
            422,
            "microphone_device_invalid",
            "The selected microphone input device is unavailable.",
        )
    return selected


def write_microphone_wav(chunk: MicrophonePCM, output_path: Path) -> Path:
    try:
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(chunk.channels)
            output.setsampwidth(chunk.sample_width)
            output.setframerate(chunk.sample_rate)
            output.writeframes(chunk.data)
    except (OSError, wave.Error) as exc:
        raise VideoExtractionError(
            500,
            "microphone_capture_failed",
            "A captured microphone chunk could not be prepared safely.",
        ) from exc
    return output_path


def microphone_pcm_is_silent(
    chunk: MicrophonePCM, peak_threshold: int
) -> bool:
    if chunk.sample_width != 2 or not chunk.data:
        return False
    samples = memoryview(chunk.data).cast("h")
    return max((abs(sample) for sample in samples), default=0) <= peak_threshold


class MicrophoneCapture:
    def __init__(
        self,
        device_id: int | None = None,
        *,
        settings: MicrophoneSettings = MICROPHONE_SETTINGS,
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
                        "microphone_device_not_found",
                        "The default Windows microphone input is unavailable.",
                    )
            else:
                device = next(
                    (item for item in devices if item.id == device_id), None
                )
                if device is None:
                    raise VideoExtractionError(
                        422,
                        "microphone_device_invalid",
                        "The selected microphone input device is unavailable.",
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
                "microphone_capture_failed",
                "The Windows microphone input stream could not be opened.",
            ) from exc

    def __enter__(self) -> MicrophoneCapture:
        return self

    def capture_chunk(
        self, should_stop: Callable[[], bool]
    ) -> MicrophonePCM | None:
        if self._stream is None:
            raise VideoExtractionError(
                503,
                "microphone_capture_failed",
                "The Windows microphone input stream is not open.",
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
                    raise OSError("microphone returned no PCM frames")
                frames.append(data)
                captured_frames += frame_count
        except VideoExtractionError:
            raise
        except Exception as exc:
            raise VideoExtractionError(
                503,
                "microphone_capture_failed",
                "Windows microphone capture failed.",
            ) from exc
        return MicrophonePCM(
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
