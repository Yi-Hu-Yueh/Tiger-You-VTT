from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.schemas.transcript import TranscriptSegment
from app.schemas.diarization import DiarizationFields


_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


class YouTubeInfoRequest(BaseModel):
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def validate_youtube_video_url(cls, url: HttpUrl) -> HttpUrl:
        parsed = urlparse(str(url))
        host = (parsed.hostname or "").lower()

        if host == "youtu.be":
            if parsed.path.strip("/"):
                return url
            raise ValueError("YouTube short URL must include a video ID")

        if host not in _YOUTUBE_HOSTS:
            raise ValueError("URL must point to a supported YouTube video")

        parts = [part for part in parsed.path.split("/") if part]
        is_watch_url = parsed.path.rstrip("/") == "/watch" and bool(
            parse_qs(parsed.query).get("v")
        )
        is_video_path = len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}
        if not (is_watch_url or is_video_path):
            raise ValueError("URL must identify a YouTube video")

        return url


class SubtitleFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ext: str
    url_available: bool


class SubtitleTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    type: Literal["manual", "auto"]
    name: str | None = None
    formats: list[SubtitleFormat]


class YouTubeVideoInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    channel: str | None = None
    uploader: str | None = None
    duration: int | None = None
    webpage_url: str
    original_url: str | None = None
    thumbnail: str | None = None
    language: str | None = None
    subtitles: list[SubtitleTrack]
    automatic_captions: list[SubtitleTrack]


class YouTubeSubtitleRequest(YouTubeInfoRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"url": "https://www.youtube.com/watch?v=Y_5hEaDzAsE"},
                {
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "en-orig",
                    "type": "auto",
                },
            ]
        }
    )

    language: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    type: Literal["manual", "auto"] | None = None
    start_time: str | None = None
    end_time: str | None = None
    enable_diarization: bool = False

    @model_validator(mode="after")
    def validate_selection_mode(self) -> "YouTubeSubtitleRequest":
        if (self.language is None) != (self.type is None):
            raise ValueError("language and type must either both be provided or both be omitted")
        return self


class YouTubeSubtitleResponse(DiarizationFields):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    language: str
    type: Literal["manual", "auto", "transcribed"]
    selection_mode: Literal["auto", "explicit", "fallback"]
    segment_count: int = Field(ge=0)
    duration: float | None = Field(default=None, ge=0)
    range_start: float | None = Field(default=None, ge=0)
    range_end: float | None = Field(default=None, ge=0)
    segments: list[TranscriptSegment]
    vtt: str
    txt: str
    srt: str
    transcription_model: str | None = Field(default=None, min_length=1)
    transcription_device: Literal["cpu", "cuda"] | None = None
    transcription_compute_type: str | None = Field(default=None, min_length=1)
    transcription_duration: float | None = Field(default=None, ge=0)


class YouTubeSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    video_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    channel: str | None = None
    duration: int | None = Field(default=None, ge=0)
    duration_text: str | None = None
    view_count: int | None = Field(default=None, ge=0)
    upload_date: str | None = None
    thumbnail: HttpUrl | None = None


class YouTubeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    sort: Literal["relevance", "upload_date", "view_count", "duration"]
    limit: int = Field(ge=1)
    candidate_count: int = Field(ge=0)
    enriched_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    native_date_order: bool
    results: list[YouTubeSearchResult]

