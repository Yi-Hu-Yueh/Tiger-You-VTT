from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.transcript import TranscriptSegment
from app.schemas.diarization import DiarizationFields


class UploadAudioTranscriptResponse(DiarizationFields):
    model_config = ConfigDict(extra="forbid")

    filename: str
    language: str
    type: Literal["transcribed"]
    selection_mode: Literal["direct"]
    segment_count: int = Field(ge=0)
    duration: float = Field(gt=0)
    range_start: float = Field(ge=0)
    range_end: float = Field(gt=0)
    segments: list[TranscriptSegment]
    vtt: str
    txt: str
    srt: str
    transcription_model: str | None = Field(default=None, min_length=1)
    transcription_device: Literal["cpu", "cuda"] | None = None
    transcription_compute_type: str | None = Field(default=None, min_length=1)
    transcription_duration: float | None = Field(default=None, ge=0)
