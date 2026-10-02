from pydantic import BaseModel, ConfigDict, Field, model_validator


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
