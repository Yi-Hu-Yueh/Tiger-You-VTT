from fastapi import APIRouter, HTTPException

from app.schemas.microphone import MicrophoneDevicesResponse
from app.services.errors import VideoExtractionError
from app.services.microphone import enumerate_microphone_devices


router = APIRouter(prefix="/api/microphone", tags=["microphone"])


@router.get("/devices", response_model=MicrophoneDevicesResponse)
def microphone_devices() -> MicrophoneDevicesResponse:
    try:
        devices = enumerate_microphone_devices()
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return MicrophoneDevicesResponse.model_validate(
        {"devices": [device.as_dict() for device in devices]}
    )
