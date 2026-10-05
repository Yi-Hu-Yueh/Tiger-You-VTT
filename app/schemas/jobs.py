from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.transcript import TranscriptSegment
from app.schemas.youtube import YouTubeInfoRequest


JobStatus = Literal[
    "queued", "running", "stopping", "stopped", "completed", "failed"
]
JobSource = Literal[
    "youtube", "upload", "audio", "system_audio", "microphone"
]


class JobStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: Literal["queued"]


class YouTubeJobRequest(YouTubeInfoRequest):
    start_time: str | None = None
    end_time: str | None = None
    end_time_is_default: bool = False
    enable_diarization: bool = False


class JobError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    source_type: JobSource
    status: JobStatus
    segment_count: int = Field(ge=0)
    txt: str
    vtt: str
    srt: str
    segments: list[TranscriptSegment]
    elapsed_seconds: float = Field(ge=0)
    range_start: float | None = Field(default=None, ge=0)
    range_end: float | None = Field(default=None, ge=0)
    result: dict[str, Any] | None = None
    error: JobError | None = None
