from __future__ import annotations

import atexit
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from app.services.errors import VideoExtractionError
from app.services.transcript import (
    transcript_to_srt,
    transcript_to_txt,
    transcript_to_vtt,
)
from app.services.video import process_uploaded_video
from app.services.youtube import get_subtitle


JobStatus = Literal[
    "queued", "running", "stopping", "stopped", "completed", "failed"
]
JobSource = Literal["youtube", "upload"]


@dataclass
class _JobRecord:
    job_id: str
    source_type: JobSource
    status: JobStatus = "queued"
    stop_requested: Event = field(default_factory=Event)
    segments: list[dict[str, Any]] = field(default_factory=list)
    txt: str = ""
    vtt: str = "WEBVTT\n\n"
    srt: str = ""
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    created_at: float = field(default_factory=monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    youtube_url: str | None = None
    filename: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    range_start: float | None = None
    range_end: float | None = None
    media_path: Path | None = None
    temporary_directory: TemporaryDirectory[str] | None = None
    future: Future[None] | None = None


class JobNotFoundError(KeyError):
    pass


class JobManager:
    def __init__(self, max_workers: int = 1) -> None:
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = RLock()
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
    ) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="upload",
            filename=filename,
            media_path=media_path,
            temporary_directory=temporary_directory,
            start_time=start_time,
            end_time=end_time,
        )
        self._submit(record)
        return {"job_id": record.job_id, "status": "queued"}

    def create_youtube_job(
        self,
        url: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, str]:
        record = _JobRecord(
            job_id=uuid4().hex,
            source_type="youtube",
            youtube_url=url,
            start_time=start_time,
            end_time=end_time,
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
            end = record.finished_at or monotonic()
            start = record.started_at or record.created_at
            return {
                "job_id": record.job_id,
                "source_type": record.source_type,
                "status": record.status,
                "segment_count": len(record.segments),
                "txt": record.txt,
                "vtt": record.vtt,
                "srt": record.srt,
                "segments": deepcopy(record.segments),
                "elapsed_seconds": round(max(0.0, end - start), 3),
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
                record.finished_at = monotonic()
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
                record.finished_at = record.finished_at or monotonic()
                record.result = record.result or self._partial_result(record)
                should_run = False
            else:
                record.status = "running"
                record.started_at = monotonic()
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

            if source_type == "upload":
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
                record.finished_at = monotonic()
        except VideoExtractionError as exc:
            self._fail_job(job_id, exc.code, exc.message)
        except Exception:
            self._fail_job(
                job_id,
                "job_processing_failed",
                "The background subtitle job failed.",
            )
        finally:
            self._cleanup_upload(job_id)

    def _fail_job(self, job_id: str, code: str, message: str) -> None:
        with self._lock:
            record = self._record(job_id)
            record.status = "failed"
            record.error = {"code": code, "message": message}
            record.result = self._partial_result(record)
            record.finished_at = monotonic()

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
