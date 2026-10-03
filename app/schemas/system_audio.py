from pydantic import BaseModel, ConfigDict, Field


class SystemAudioDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    name: str = Field(min_length=1)
    is_default: bool
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)


class SystemAudioDevicesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: list[SystemAudioDeviceResponse]


class SystemAudioJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: int | None = Field(default=None, ge=0)
