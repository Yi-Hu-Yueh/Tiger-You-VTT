from __future__ import annotations

import atexit
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock
from time import monotonic
from typing import Any, Callable, Literal
from uuid import uuid4

from app.services.audio import process_uploaded_audio
from app.services.errors import VideoExtractionError
from app.services.media_range import offset_clip_segment
from app.services.microphone import (
    MicrophoneCapture,
    microphone_pcm_is_silent,
    write_microphone_wav,
)
from app.services.system_audio import (
    SystemAudioCapture,
    pcm_is_silent,
    write_pcm_wav,
)
from app.services.transcript import (
    transcript_to_srt,
    transcript_to_txt,
    transcript_to_vtt,
)
from app.services.video import process_uploaded_video
from app.services.whisper import transcribe_audio
from app.services.youtube import get_subtitle


logger = logging.getLogger(__name__)


JobStatus = Literal[
    "queued", "running", "stopping", "stopped", "completed", "failed"
]
JobSource = Literal[
    "youtube", "upload", "audio", "system_audio", "microphone"
]


@dataclass
class _JobRecord:
    job_id: str
    source_type: JobSource
    created_at: float
    status: JobStatus = "queued"
    stop_requested: Event = field(default_factory=Event)
    segments: list[dict[str, Any]] = field(default_factory=list)
    txt: str = ""
    vtt: str = "WEBVTT\n\n"
    srt: str = ""
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    started_at: float | None = None
    finished_at: float | None = None
    youtube_url: str | None = None
    filename: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    end_time_is_default: bool = False
    enable_diarization: bool = False
    device_id: int | None = None
    range_start: float | None = None
    range_end: float | None = None
    media_path: Path | None = None
    temporary_directory: TemporaryDirectory[str] | None = None
    future: Future[None] | None = None


class JobNotFoundError(KeyError):
    pass


class JobManager:
    def __init__(
        self,
        max_workers: int = 1,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = RLock()
        self._clock = clock
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="tiger-you-vtt-job",
        )

    def create_video_job(
        self,
        temporary_directory: TemporaryDirectory[str],
        media_path: Path,
        filename: str,
        start_time: str | None = None,
        end_time: str | None = None,
        end_time_is_default: bool = False,
        enable_diarization: bool = False,
    ) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="upload",
            created_at=self._clock(),
            filename=filename,
            media_path=media_path,
            temporary_directory=temporary_directory,
            start_time=start_time,
            end_time=end_time,
            end_time_is_default=end_time_is_default,
            enable_diarization=enable_diarization,
        )
        self._submit(record)
        return {"job_id": record.job_id, "status": "queued"}

    def create_audio_job(
        self,
        temporary_directory: TemporaryDirectory[str],
        media_path: Path,
        filename: str,
        start_time: str | None = None,
        end_time: str | None = None,
        end_time_is_default: bool = False,
        enable_diarization: bool = False,
    ) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="audio",
            created_at=self._clock(),
            filename=filename,
            media_path=media_path,
            temporary_directory=temporary_directory,
            start_time=start_time,
            end_time=end_time,
            end_time_is_default=end_time_is_default,
            enable_diarization=enable_diarization,
        )
        self._submit(record)
        return {"job_id": record.job_id, "status": "queued"}

    def create_youtube_job(
        self,
        url: str,
        start_time: str | None = None,
        end_time: str | None = None,
        end_time_is_default: bool = False,
        enable_diarization: bool = False,
    ) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="youtube",
            created_at=self._clock(),
            youtube_url=url,
            start_time=start_time,
            end_time=end_time,
            end_time_is_default=end_time_is_default,
            enable_diarization=enable_diarization,
        )
        self._submit(record)
        return {"job_id": record.job_id, "status": "queued"}

    def create_system_audio_job(self, device_id: int) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="system_audio",
            created_at=self._clock(),
            device_id=device_id,
        )
        self._submit(record)
        return {"job_id": record.job_id, "status": "queued"}

    def create_microphone_job(self, device_id: int) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="microphone",
            created_at=self._clock(),
            device_id=device_id,
        )
        self._submit(record)
        return {"job_id": record.job_id, "status": "queued"}

    def _submit(self, record: _JobRecord) -> None:
        with self._lock:
            self._jobs[record.job_id] = record
            record.future = self._executor.submit(
                self._run_job, record.job_id
            )

    def _record(self, job_id: str) -> _JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(job_id)
            end = (
                record.finished_at
                if record.finished_at is not None
                else self._clock()
            )
            return {
                "job_id": record.job_id,
                "source_type": record.source_type,
                "status": record.status,
                "segment_count": len(record.segments),
                "txt": record.txt,
                "vtt": record.vtt,
                "srt": record.srt,
                "segments": deepcopy(record.segments),
                "elapsed_seconds": round(
                    max(0.0, end - record.created_at), 3
                ),
                "range_start": record.range_start,
                "range_end": record.range_end,
                "result": deepcopy(record.result),
                "error": deepcopy(record.error),
            }

    def stop(self, job_id: str) -> dict[str, Any]:
        cleanup = False
        with self._lock:
            record = self._record(job_id)
            if record.status == "queued":
                record.stop_requested.set()
                record.status = "stopped"
                record.finished_at = self._clock()
                record.result = self._partial_result(record)
                cleanup = True
            elif record.status == "running":
                record.stop_requested.set()
                record.status = "stopping"
            elif record.status == "stopping":
                record.stop_requested.set()

        if cleanup:
            self._cleanup_upload(job_id)
        return self.snapshot(job_id)

    def _on_segment(self, job_id: str, segment: dict[str, Any]) -> None:
        with self._lock:
            record = self._record(job_id)
            record.segments.append(dict(segment))
            record.txt = transcript_to_txt(record.segments)
            record.vtt = transcript_to_vtt(record.segments)
            record.srt = transcript_to_srt(record.segments)

    def _should_stop(self, job_id: str) -> bool:
        with self._lock:
            return self._record(job_id).stop_requested.is_set()

    def _on_range_resolved(
        self, job_id: str, range_start: float, range_end: float
    ) -> None:
        with self._lock:
            record = self._record(job_id)
            record.range_start = range_start
            record.range_end = range_end

    def _on_system_audio_segment(
        self, job_id: str, segment: dict[str, Any]
    ) -> None:
        with self._lock:
            record = self._record(job_id)
            if record.segments and float(segment["start"]) < float(
                record.segments[-1]["start"]
            ):
                raise VideoExtractionError(
                    502,
                    "system_audio_capture_failed",
                    "System-audio transcript timestamps were not chronological.",
                )
        self._on_segment(job_id, segment)

    def _on_microphone_segment(
        self, job_id: str, segment: dict[str, Any]
    ) -> None:
        with self._lock:
            record = self._record(job_id)
            if record.segments and float(segment["start"]) < float(
                record.segments[-1]["start"]
            ):
                raise VideoExtractionError(
                    502,
                    "microphone_capture_failed",
                    "Microphone transcript timestamps were not chronological.",
                )
        self._on_segment(job_id, segment)

    @staticmethod
    def _partial_result(record: _JobRecord) -> dict[str, Any]:
        return {
            "segment_count": len(record.segments),
            "segments": deepcopy(record.segments),
            "txt": record.txt,
            "vtt": record.vtt,
            "srt": record.srt,
            "range_start": record.range_start,
            "range_end": record.range_end,
        }

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            record = self._record(job_id)
            if record.stop_requested.is_set() or record.status == "stopped":
                record.status = "stopped"
                record.finished_at = (
                    record.finished_at
                    if record.finished_at is not None
                    else self._clock()
                )
                record.result = record.result or self._partial_result(record)
                should_run = False
            else:
                record.status = "running"
                record.started_at = self._clock()
                should_run = True

        if not should_run:
            self._cleanup_upload(job_id)
            return

        try:
            with self._lock:
                record = self._record(job_id)
                source_type = record.source_type
                media_path = record.media_path
                filename = record.filename
                temporary_directory = record.temporary_directory
                youtube_url = record.youtube_url
                start_time = record.start_time
                end_time = record.end_time
                end_time_is_default = record.end_time_is_default
                enable_diarization = record.enable_diarization
                device_id = record.device_id

            if source_type == "system_audio":
                if device_id is None:
                    raise RuntimeError(
                        "System-audio job is missing its output device"
                    )
                result = self._run_system_audio_capture(job_id, device_id)
            elif source_type == "microphone":
                if device_id is None:
                    raise RuntimeError(
                        "Microphone job is missing its input device"
                    )
                result = self._run_microphone_capture(job_id, device_id)
            elif source_type in {"upload", "audio"}:
                if (
                    media_path is None
                    or filename is None
                    or temporary_directory is None
                ):
                    raise RuntimeError("Upload job is missing its media")
                upload_arguments = {
                    "on_segment": lambda segment: self._on_segment(
                        job_id, segment
                    ),
                    "should_stop": lambda: self._should_stop(job_id),
                    "on_range_resolved": lambda start, end: (
                        self._on_range_resolved(job_id, start, end)
                    ),
                }
                if (start_time or "").strip() or (end_time or "").strip():
                    upload_arguments.update(
                        {
                            "start_time": start_time,
                            "end_time": end_time,
                        }
                    )
                if end_time_is_default:
                    upload_arguments["end_time_is_default"] = True
                if enable_diarization:
                    upload_arguments["enable_diarization"] = True
                if source_type == "audio":
                    result = process_uploaded_audio(
                        media_path,
                        filename,
                        **upload_arguments,
                    )
                else:
                    result = process_uploaded_video(
                        media_path,
                        filename,
                        Path(temporary_directory.name),
                        **upload_arguments,
                    )
            else:
                if youtube_url is None:
                    raise RuntimeError("YouTube job is missing its URL")
                youtube_arguments = {
                    "on_segment": lambda segment: self._on_segment(
                        job_id, segment
                    ),
                    "should_stop": lambda: self._should_stop(job_id),
                    "on_range_resolved": lambda start, end: (
                        self._on_range_resolved(job_id, start, end)
                    ),
                }
                if (start_time or "").strip() or (end_time or "").strip():
                    youtube_arguments.update(
                        {
                            "start_time": start_time,
                            "end_time": end_time,
                        }
                    )
                if end_time_is_default:
                    youtube_arguments["end_time_is_default"] = True
                if enable_diarization:
                    youtube_arguments["enable_diarization"] = True
                result = get_subtitle(youtube_url, **youtube_arguments)

            clean_result = dict(result)
            stopped = bool(clean_result.pop("_stopped", False))
            with self._lock:
                record = self._record(job_id)
                result_segments = clean_result.get("segments")
                if isinstance(result_segments, list):
                    record.segments = deepcopy(result_segments)
                record.txt = str(clean_result.get("txt", ""))
                record.vtt = str(
                    clean_result.get(
                        "vtt", transcript_to_vtt(record.segments)
                    )
                )
                record.srt = str(clean_result.get("srt", ""))
                record.result = clean_result
                result_range_start = clean_result.get("range_start")
                result_range_end = clean_result.get("range_end")
                if isinstance(result_range_start, (int, float)):
                    record.range_start = float(result_range_start)
                if isinstance(result_range_end, (int, float)):
                    record.range_end = float(result_range_end)
                record.status = "stopped" if stopped else "completed"
                record.finished_at = self._clock()
        except VideoExtractionError as exc:
            logger.exception(
                "Background job %s failed with controlled error %s",
                job_id,
                exc.code,
            )
            self._fail_job(job_id, exc.code, exc.message)
        except Exception:
            logger.exception("Background job %s failed unexpectedly", job_id)
            self._fail_job(
                job_id,
                "job_processing_failed",
                "The background subtitle job failed.",
            )
        finally:
            self._cleanup_upload(job_id)

    def _run_system_audio_capture(
        self, job_id: str, device_id: int
    ) -> dict[str, Any]:
        session_offset = 0.0
        language = "und"
        transcription_model: str | None = None
        transcription_device: str | None = None
        transcription_compute_type: str | None = None
        transcription_duration = 0.0
        processed_chunks = 0

        with TemporaryDirectory(
            prefix="tiger-you-vtt-system-audio-"
        ) as temporary_directory:
            working_directory = Path(temporary_directory)
            with SystemAudioCapture(device_id) as capture:
                capture_device = capture.device.as_dict()
                chunk_seconds = capture.settings.chunk_seconds
                while not self._should_stop(job_id):
                    chunk = capture.capture_chunk(
                        lambda: self._should_stop(job_id)
                    )
                    if chunk is None or self._should_stop(job_id):
                        break
                    chunk_end = session_offset + chunk.duration_seconds
                    if pcm_is_silent(
                        chunk, capture.settings.silence_peak_threshold
                    ):
                        processed_chunks += 1
                        session_offset = chunk_end
                        continue
                    chunk_path = working_directory / "current-chunk.wav"
                    write_pcm_wav(chunk, chunk_path)

                    def on_chunk_segment(segment: dict[str, Any]) -> None:
                        translated = offset_clip_segment(
                            segment, session_offset, chunk_end
                        )
                        if translated is not None:
                            self._on_system_audio_segment(job_id, translated)

                    try:
                        transcription = transcribe_audio(
                            chunk_path,
                            on_chunk_segment,
                            lambda: self._should_stop(job_id),
                            retry_without_vad=False,
                            allow_empty=True,
                        )
                    finally:
                        try:
                            chunk_path.unlink(missing_ok=True)
                        except OSError as exc:
                            raise VideoExtractionError(
                                500,
                                "system_audio_capture_failed",
                                "A temporary system-audio chunk could not be removed.",
                            ) from exc

                    processed_chunks += 1
                    session_offset = chunk_end
                    if language == "und" and transcription.language != "und":
                        language = transcription.language
                    transcription_model = transcription.model
                    transcription_device = transcription.device
                    transcription_compute_type = transcription.compute_type
                    transcription_duration += transcription.duration_seconds
                    if transcription.stopped or self._should_stop(job_id):
                        break

        with self._lock:
            record = self._record(job_id)
            segments = deepcopy(record.segments)
            txt = record.txt
            vtt = record.vtt
            srt = record.srt
        result: dict[str, Any] = {
            "_stopped": True,
            "language": language,
            "type": "transcribed",
            "selection_mode": "direct",
            "segment_count": len(segments),
            "duration": round(session_offset, 3),
            "range_start": 0.0,
            "range_end": round(session_offset, 3),
            "segments": segments,
            "vtt": vtt,
            "txt": txt,
            "srt": srt,
            "capture_device": capture_device,
            "capture_chunk_seconds": chunk_seconds,
            "processed_chunks": processed_chunks,
            "transcription_duration": round(transcription_duration, 3),
        }
        if transcription_model is not None:
            result.update(
                {
                    "transcription_model": transcription_model,
                    "transcription_device": transcription_device,
                    "transcription_compute_type": transcription_compute_type,
                }
            )
        return result

    def _run_microphone_capture(
        self, job_id: str, device_id: int
    ) -> dict[str, Any]:
        session_offset = 0.0
        language = "und"
        transcription_model: str | None = None
        transcription_device: str | None = None
        transcription_compute_type: str | None = None
        transcription_duration = 0.0
        processed_chunks = 0

        with TemporaryDirectory(
            prefix="tiger-you-vtt-microphone-"
        ) as temporary_directory:
            working_directory = Path(temporary_directory)
            with MicrophoneCapture(device_id) as capture:
                capture_device = capture.device.as_dict()
                chunk_seconds = capture.settings.chunk_seconds
                while not self._should_stop(job_id):
                    chunk = capture.capture_chunk(
                        lambda: self._should_stop(job_id)
                    )
                    if chunk is None or self._should_stop(job_id):
                        break
                    chunk_end = session_offset + chunk.duration_seconds
                    if microphone_pcm_is_silent(
                        chunk, capture.settings.silence_peak_threshold
                    ):
                        processed_chunks += 1
                        session_offset = chunk_end
                        continue
                    chunk_path = working_directory / "current-chunk.wav"
                    write_microphone_wav(chunk, chunk_path)

                    def on_chunk_segment(segment: dict[str, Any]) -> None:
                        translated = offset_clip_segment(
                            segment, session_offset, chunk_end
                        )
                        if translated is not None:
                            self._on_microphone_segment(job_id, translated)

                    try:
                        transcription = transcribe_audio(
                            chunk_path,
                            on_chunk_segment,
                            lambda: self._should_stop(job_id),
                            retry_without_vad=False,
                            allow_empty=True,
                        )
                    finally:
                        try:
                            chunk_path.unlink(missing_ok=True)
                        except OSError as exc:
                            raise VideoExtractionError(
                                500,
                                "microphone_capture_failed",
                                "A temporary microphone chunk could not be removed.",
                            ) from exc

                    processed_chunks += 1
                    session_offset = chunk_end
                    if language == "und" and transcription.language != "und":
                        language = transcription.language
                    transcription_model = transcription.model
                    transcription_device = transcription.device
                    transcription_compute_type = transcription.compute_type
                    transcription_duration += transcription.duration_seconds
                    if transcription.stopped or self._should_stop(job_id):
                        break

        with self._lock:
            record = self._record(job_id)
            segments = deepcopy(record.segments)
            txt = record.txt
            vtt = record.vtt
            srt = record.srt
        result: dict[str, Any] = {
            "_stopped": True,
            "language": language,
            "type": "transcribed",
            "selection_mode": "direct",
            "segment_count": len(segments),
            "duration": round(session_offset, 3),
            "range_start": 0.0,
            "range_end": round(session_offset, 3),
            "segments": segments,
            "vtt": vtt,
            "txt": txt,
            "srt": srt,
            "capture_device": capture_device,
            "capture_chunk_seconds": chunk_seconds,
            "processed_chunks": processed_chunks,
            "transcription_duration": round(transcription_duration, 3),
        }
        if transcription_model is not None:
            result.update(
                {
                    "transcription_model": transcription_model,
                    "transcription_device": transcription_device,
                    "transcription_compute_type": transcription_compute_type,
                }
            )
        return result

    def _fail_job(self, job_id: str, code: str, message: str) -> None:
        with self._lock:
            record = self._record(job_id)
            record.status = "failed"
            record.error = {"code": code, "message": message}
            record.result = self._partial_result(record)
            record.finished_at = self._clock()

    def _cleanup_upload(self, job_id: str) -> None:
        with self._lock:
            record = self._record(job_id)
            temporary_directory = record.temporary_directory
            record.temporary_directory = None
            record.media_path = None
        if temporary_directory is None:
            return
        try:
            temporary_directory.cleanup()
        except OSError:
            self._fail_job(
                job_id,
                "upload_cleanup_failed",
                "Temporary uploaded media could not be removed.",
            )

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


JOB_MANAGER = JobManager(max_workers=1)
atexit.register(JOB_MANAGER.shutdown)
