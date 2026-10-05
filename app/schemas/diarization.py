from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiarizedTranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    speaker: str | None = None


class DiarizationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class DiarizationFields(BaseModel):
    diarization_enabled: bool | None = None
    diarization_status: Literal["completed", "failed", "stopped"] | None = None
    speaker_count: int | None = Field(default=None, ge=0)
    diarized_segments: list[DiarizedTranscriptSegment] | None = None
    speaker_txt: str | None = None
    speaker_vtt: str | None = None
    speaker_srt: str | None = None
    diarization_model: str | None = None
    diarization_device: Literal["cpu", "cuda"] | None = None
    diarization_duration: float | None = Field(default=None, ge=0)
    diarization_error: DiarizationFailure | None = None
