from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.transcript import TranscriptSegment


JobStatus = Literal[
    "queued", "running", "stopping", "stopped", "completed", "failed"
]
JobSource = Literal["youtube", "upload"]


class JobStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: Literal["queued"]


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
    result: dict[str, Any] | None = None
    error: JobError | None = None
