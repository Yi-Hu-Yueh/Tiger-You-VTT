import wave

import pytest

from app.config import MicrophoneSettings
from app.services.errors import VideoExtractionError
from app.services.microphone import (
    MicrophoneCapture,
    MicrophonePCM,
    enumerate_microphone_devices,
    microphone_pcm_is_silent,
    select_microphone_device,
    write_microphone_wav,
)


def microphone(device_id=1, *, default_rate=48000.0, channels=1):
    return {
        "index": device_id,
        "name": "Microphone",
        "maxInputChannels": channels,
        "maxOutputChannels": 0,
        "defaultSampleRate": default_rate,
        "isLoopbackDevice": False,
    }


def loopback(device_id=5):
    return {
        "index": device_id,
        "name": "Speakers [Loopback]",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 48000.0,
        "isLoopbackDevice": True,
    }


def output(device_id=8):
    return {
        "index": device_id,
        "name": "Speakers",
        "maxInputChannels": 0,
        "maxOutputChannels": 2,
        "defaultSampleRate": 48000.0,
        "isLoopbackDevice": False,
    }


class FakeStream:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.reads = []
        self.stopped = False
        self.closed = False

    def read(self, frames, exception_on_overflow=False):
        self.reads.append((frames, exception_on_overflow))
        if self.fail:
            raise OSError("device disconnected")
        return b"\x01\x00" * frames

    def is_active(self):
        return not self.stopped

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeAudio:
    def __init__(self, devices=None, default_id=1, stream=None):
        self.devices = list([microphone()] if devices is None else devices)
        self.default_id = default_id
        self.stream = stream or FakeStream()
        self.open_kwargs = None
        self.terminated = False

    def get_default_input_device_info(self):
        if self.default_id is None:
            raise OSError("no default")
        return next(d for d in self.devices if d["index"] == self.default_id)

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def get_sample_size(self, _format):
        return 2

    def open(self, **kwargs):
        self.open_kwargs = kwargs
        return self.stream

    def terminate(self):
        self.terminated = True


class FakeModule:
    paInt16 = 8


def test_enumeration_returns_only_microphones_and_marks_default() -> None:
    audio = FakeAudio(
        [microphone(1), loopback(), output(), microphone(3)], default_id=3
    )
    devices = enumerate_microphone_devices(
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    )
    assert [device.id for device in devices] == [1, 3]
    assert [device.is_default for device in devices] == [False, True]
    assert all("Loopback" not in device.name for device in devices)
    assert devices[1].sample_rate == 48000
    assert devices[1].channels == 1
    assert audio.terminated is True


def test_non_windows_and_missing_devices_are_controlled() -> None:
    with pytest.raises(VideoExtractionError) as unsupported:
        enumerate_microphone_devices(platform_name="posix")
    assert unsupported.value.code == "microphone_unsupported"

    audio = FakeAudio([loopback(), output()], default_id=None)
    with pytest.raises(VideoExtractionError) as missing:
        enumerate_microphone_devices(
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: audio,
            platform_name="nt",
        )
    assert missing.value.code == "microphone_device_not_found"


def test_device_selection_uses_default_and_rejects_invalid_id() -> None:
    selected = select_microphone_device(
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: FakeAudio(
            [microphone(1), microphone(3)], default_id=3
        ),
        platform_name="nt",
    )
    assert selected.id == 3
    with pytest.raises(VideoExtractionError) as error:
        select_microphone_device(
            99,
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: FakeAudio(),
            platform_name="nt",
        )
    assert error.value.code == "microphone_device_invalid"


def test_missing_default_does_not_fall_back_to_arbitrary_input() -> None:
    with pytest.raises(VideoExtractionError) as error:
        select_microphone_device(
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: FakeAudio(
                [microphone(1)], default_id=None
            ),
            platform_name="nt",
        )
    assert error.value.code == "microphone_device_not_found"


def test_capture_reads_bounded_pcm_and_writes_valid_wav(tmp_path) -> None:
    stream = FakeStream()
    audio = FakeAudio(
        [microphone(default_rate=1000.0)], default_id=1, stream=stream
    )
    settings = MicrophoneSettings(0.01, 4)
    with MicrophoneCapture(
        settings=settings,
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    ) as capture:
        chunk = capture.capture_chunk(lambda: False)
        assert chunk is not None
        assert chunk.frame_count == 10
        assert chunk.duration_seconds == 0.01
        assert stream.reads == [(4, False), (4, False), (2, False)]
        assert audio.open_kwargs == {
            "format": 8,
            "channels": 1,
            "rate": 1000,
            "input": True,
            "input_device_index": 1,
            "frames_per_buffer": 4,
        }
        path = write_microphone_wav(chunk, tmp_path / "chunk.wav")
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 1000
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == 10
        path.unlink()
        assert not path.exists()
    assert stream.stopped and stream.closed and audio.terminated


def test_stop_prevents_pcm_read_and_capture_failure_is_controlled() -> None:
    stream = FakeStream()
    audio = FakeAudio(stream=stream)
    with MicrophoneCapture(
        settings=MicrophoneSettings(10.0, 1024),
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    ) as capture:
        assert capture.capture_chunk(lambda: True) is None
        assert stream.reads == []

    failed_stream = FakeStream(fail=True)
    failed_audio = FakeAudio(stream=failed_stream)
    with pytest.raises(VideoExtractionError) as error:
        with MicrophoneCapture(
            settings=MicrophoneSettings(0.01, 4),
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: failed_audio,
            platform_name="nt",
        ) as capture:
            capture.capture_chunk(lambda: False)
    assert error.value.code == "microphone_capture_failed"
    assert failed_stream.closed and failed_audio.terminated


def test_microphone_silence_threshold_is_conservative() -> None:
    silent = MicrophonePCM(b"\x00\x00" * 8, 8000, 1, 2, 8)
    quiet_speech = MicrophonePCM(b"\x20\x00" * 8, 8000, 1, 2, 8)
    assert microphone_pcm_is_silent(silent, 16) is True
    assert microphone_pcm_is_silent(quiet_speech, 16) is False
