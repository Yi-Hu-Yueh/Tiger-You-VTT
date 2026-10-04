from pydantic import BaseModel, ConfigDict, Field


class MicrophoneDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    name: str = Field(min_length=1)
    is_default: bool
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)


class MicrophoneDevicesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: list[MicrophoneDeviceResponse]


class MicrophoneJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: int | None = Field(default=None, ge=0)
