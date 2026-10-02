from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


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

    @model_validator(mode="after")
    def validate_selection_mode(self) -> "YouTubeSubtitleRequest":
        if (self.language is None) != (self.type is None):
            raise ValueError("language and type must either both be provided or both be omitted")
        return self


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptSegment":
        if self.end < self.start:
            raise ValueError("segment end must be greater than or equal to start")
        return self


class YouTubeSubtitleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    title: str
    language: str
    type: Literal["manual", "auto"]
    selection_mode: Literal["auto", "explicit"]
    segment_count: int = Field(ge=0)
    duration: float | None = Field(default=None, ge=0)
    segments: list[TranscriptSegment]
    vtt: str
    txt: str
    srt: str

