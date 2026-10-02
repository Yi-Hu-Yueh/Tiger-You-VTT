from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.transcript import TranscriptSegment


class UploadVideoSubtitleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    language: str
    type: Literal["embedded", "transcribed"]
    selection_mode: Literal["auto", "fallback"]
    segment_count: int = Field(ge=0)
    duration: float | None = Field(default=None, ge=0)
    segments: list[TranscriptSegment]
    vtt: str
    txt: str
    srt: str
    transcription_model: str | None = Field(default=None, min_length=1)
    transcription_device: Literal["cpu", "cuda"] | None = None
    transcription_compute_type: str | None = Field(default=None, min_length=1)
    transcription_duration: float | None = Field(default=None, ge=0)
