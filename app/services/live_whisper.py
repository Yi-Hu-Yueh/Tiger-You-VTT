"""Lazy, local-only Turbo runtime; leased by one live consumer at a time."""
from time import monotonic

from app.services.errors import VideoExtractionError
from app.services.inference_runtime import inference_session
from app.services.whisper import _configure_windows_cuda_runtime


class LiveWhisper:
    def __init__(self, factory=None):
        self._factory = factory
        self._model = None
        self._ready = False

    def release(self):
        self._model = None
        self._ready = False

    def session(self, should_stop):
        return inference_session("live-turbo", self.release, should_stop)

    def prepare(self, should_stop):
        started = monotonic()
        metrics = {"model_load_seconds": 0.0, "warmup_seconds": [], "model_cached": self._ready}
        if self._ready:
            return {**metrics, "preparation_seconds": monotonic()-started, "model_ready": True}
        try:
            _configure_windows_cuda_runtime()
            if self._factory is None:
                from faster_whisper import WhisperModel
                factory = WhisperModel
            else:
                factory = self._factory
            self._model = factory("large-v3-turbo", device="cuda", compute_type="int8_float32", local_files_only=True)
            if self._model.model.device != "cuda" or self._model.model.compute_type != "int8_float32":
                raise RuntimeError("The required CUDA compute configuration was not activated")
            metrics["model_load_seconds"] = monotonic()-started
            import numpy as np

            # Non-user calibration waveform. Disable VAD ONLY here so both real
            # decode passes exercise lazy encoder/decoder kernels, even in silence.
            t = np.arange(32000, dtype=np.float32) / 16000
            calibration = (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
            # The live path also needs Silero's lazy ONNX session. Prime it
            # before capture, even when the calibration tone is rejected as speech.
            from faster_whisper.vad import get_speech_timestamps
            vad_started = monotonic()
            get_speech_timestamps(calibration)
            metrics["vad_prepare_seconds"] = monotonic()-vad_started
            for _ in range(2):
                if should_stop():
                    self.release()
                    return {**metrics, "model_ready": False, "preparation_seconds": monotonic()-started}
                inference_start = monotonic()
                segments, _info = self._model.transcribe(calibration, beam_size=5, vad_filter=False, language="en", condition_on_previous_text=False)
                list(segments)
                metrics["warmup_seconds"].append(monotonic()-inference_start)
            self._ready = True
            return {**metrics, "preparation_seconds": monotonic()-started, "model_ready": True}
        except Exception as exc:
            self.release()
            raise VideoExtractionError(503, "low_latency_model_unavailable", "The low-latency model could not be prepared. Normal System Audio remains available.") from exc

    def transcribe(self, pcm, on_segment):
        try:
            import io
            import wave

            # Bounded in-memory WAV; no session recording or disk artifact.
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as output:
                output.setnchannels(pcm.channels)
                output.setsampwidth(pcm.sample_width)
                output.setframerate(pcm.sample_rate)
                output.writeframes(pcm.data)
            buffer.seek(0)
            generated, info = self._model.transcribe(buffer, beam_size=5, vad_filter=True, condition_on_previous_text=False)
            from app.services.transcript import normalize_transcribed_segments

            for segment in generated:
                for normalized in normalize_transcribed_segments([{"start": segment.start, "end": segment.end, "text": segment.text}]):
                    on_segment(normalized)
            return getattr(info, "language", "und")
        except Exception as exc:
            self.release()
            raise VideoExtractionError(502, "low_latency_inference_failed", "Low-latency transcription failed; completed subtitles were retained.") from exc


LIVE_TRANSCRIBER = LiveWhisper()
