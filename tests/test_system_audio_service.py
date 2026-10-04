import wave

import pytest

from app.config import SystemAudioSettings
from app.services.errors import VideoExtractionError
from app.services.system_audio import (
    CapturedPCM,
    SystemAudioCapture,
    enumerate_system_audio_devices,
    pcm_is_silent,
    select_system_audio_device,
    write_pcm_wav,
)


def loopback(device_id=5, *, default_rate=48000.0, channels=2):
    return {
        "index": device_id,
        "name": "Speakers [Loopback]",
        "hostApi": 2,
        "maxInputChannels": channels,
        "maxOutputChannels": 0,
        "defaultSampleRate": default_rate,
        "isLoopbackDevice": True,
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
        return b"\x01\x00" * frames * 2

    def is_active(self):
        return not self.stopped

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeAudio:
    def __init__(self, devices=None, default_id=5, stream=None):
        self.devices = list(devices or [loopback()])
        self.default_id = default_id
        self.stream = stream or FakeStream()
        self.open_kwargs = None
        self.terminated = False

    def get_default_wasapi_loopback(self):
        if self.default_id is None:
            raise OSError("no default")
        return next(d for d in self.devices if d["index"] == self.default_id)

    def get_loopback_device_info_generator(self):
        yield from self.devices

    def get_sample_size(self, _format):
        return 2

    def open(self, **kwargs):
        self.open_kwargs = kwargs
        return self.stream

    def terminate(self):
        self.terminated = True


class FakeModule:
    paInt16 = 8


def test_enumeration_returns_only_usable_loopbacks_and_marks_default() -> None:
    microphone = {
        "index": 2,
        "name": "Microphone",
        "maxInputChannels": 1,
        "defaultSampleRate": 44100,
        "isLoopbackDevice": False,
    }
    audio = FakeAudio([microphone, loopback(), loopback(7)], default_id=5)
    devices = enumerate_system_audio_devices(
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    )
    assert [device.id for device in devices] == [5, 7]
    assert [device.is_default for device in devices] == [True, False]
    assert devices[0].sample_rate == 48000
    assert devices[0].channels == 2
    assert all("Microphone" not in device.name for device in devices)
    assert audio.terminated is True


def test_non_windows_and_missing_devices_are_controlled() -> None:
    with pytest.raises(VideoExtractionError) as unsupported:
        enumerate_system_audio_devices(platform_name="posix")
    assert unsupported.value.code == "system_audio_unsupported"

    audio = FakeAudio([], default_id=None)
    audio.devices = []
    with pytest.raises(VideoExtractionError) as missing:
        enumerate_system_audio_devices(
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: audio,
            platform_name="nt",
        )
    assert missing.value.code == "system_audio_device_not_found"


def test_device_selection_uses_default_and_rejects_invalid_id() -> None:
    audio = FakeAudio([loopback(5), loopback(7)], default_id=7)
    selected = select_system_audio_device(
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    )
    assert selected.id == 7
    with pytest.raises(VideoExtractionError) as error:
        select_system_audio_device(
            99,
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: FakeAudio(),
            platform_name="nt",
        )
    assert error.value.code == "system_audio_device_invalid"


def test_capture_reads_bounded_pcm_and_writes_valid_wav(tmp_path) -> None:
    stream = FakeStream()
    audio = FakeAudio(
        [loopback(default_rate=1000.0)], default_id=5, stream=stream
    )
    settings = SystemAudioSettings(chunk_seconds=0.01, frames_per_buffer=4)
    with SystemAudioCapture(
        settings=settings,
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    ) as capture:
        chunk = capture.capture_chunk(lambda: False)
        assert chunk is not None
        assert chunk.frame_count == 10
        assert chunk.duration_seconds == 0.01
        assert len(chunk.data) == 10 * 2 * 2
        assert stream.reads == [(4, False), (4, False), (2, False)]
        assert audio.open_kwargs == {
            "format": 8,
            "channels": 2,
            "rate": 1000,
            "input": True,
            "input_device_index": 5,
            "frames_per_buffer": 4,
        }
        output = write_pcm_wav(chunk, tmp_path / "chunk.wav")
        with wave.open(str(output), "rb") as wav:
            assert wav.getframerate() == 1000
            assert wav.getnchannels() == 2
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == 10
    assert stream.stopped and stream.closed and audio.terminated


def test_capture_stops_promptly_without_returning_partial_chunk() -> None:
    stream = FakeStream()
    audio = FakeAudio(stream=stream)
    with SystemAudioCapture(
        settings=SystemAudioSettings(10.0, 1024),
        pyaudio_module=FakeModule,
        pyaudio_factory=lambda: audio,
        platform_name="nt",
    ) as capture:
        assert capture.capture_chunk(lambda: True) is None
        assert stream.reads == []


def test_capture_device_failure_is_controlled_and_resources_close() -> None:
    stream = FakeStream(fail=True)
    audio = FakeAudio(stream=stream)
    with pytest.raises(VideoExtractionError) as error:
        with SystemAudioCapture(
            settings=SystemAudioSettings(0.01, 4),
            pyaudio_module=FakeModule,
            pyaudio_factory=lambda: audio,
            platform_name="nt",
        ) as capture:
            capture.capture_chunk(lambda: False)
    assert error.value.code == "system_audio_capture_failed"
    assert stream.closed and audio.terminated


def test_write_pcm_wav_does_not_persist_after_caller_cleanup(tmp_path) -> None:
    chunk = CapturedPCM(b"\x00\x00" * 8, 8000, 1, 2, 8)
    path = write_pcm_wav(chunk, tmp_path / "current.wav")
    assert path.is_file()
    path.unlink()
    assert not path.exists()


def test_pcm_silence_threshold_is_conservative() -> None:
    silent = CapturedPCM(b"\x00\x00" * 8, 8000, 1, 2, 8)
    quiet_speech = CapturedPCM(b"\x20\x00" * 8, 8000, 1, 2, 8)
    assert pcm_is_silent(silent, 16) is True
    assert pcm_is_silent(quiet_speech, 16) is False
