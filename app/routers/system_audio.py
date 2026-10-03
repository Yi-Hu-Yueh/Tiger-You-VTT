from fastapi import APIRouter, HTTPException

from app.schemas.system_audio import SystemAudioDevicesResponse
from app.services.errors import VideoExtractionError
from app.services.system_audio import enumerate_system_audio_devices


router = APIRouter(prefix="/api/system-audio", tags=["system-audio"])


@router.get("/devices", response_model=SystemAudioDevicesResponse)
def system_audio_devices() -> SystemAudioDevicesResponse:
    try:
        devices = enumerate_system_audio_devices()
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return SystemAudioDevicesResponse.model_validate(
        {"devices": [device.as_dict() for device in devices]}
    )
