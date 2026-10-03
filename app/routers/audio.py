from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.schemas.audio import UploadAudioTranscriptResponse
from app.services.audio import (
    process_uploaded_audio,
    safe_audio_upload_name,
    store_audio_upload,
)
from app.services.errors import VideoExtractionError


router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.post(
    "/transcript",
    response_model=UploadAudioTranscriptResponse,
    response_model_exclude_none=True,
)
async def audio_transcript(
    file: UploadFile = File(..., description="A local audio file"),
    start_time: str | None = Form(default=None),
    end_time: str | None = Form(default=None),
) -> UploadAudioTranscriptResponse:
    try:
        display_name, suffix = safe_audio_upload_name(file.filename)
        with TemporaryDirectory(
            prefix="tiger-you-vtt-audio-upload-"
        ) as temporary_directory:
            media_path = Path(temporary_directory) / f"upload{suffix}"
            await store_audio_upload(file, media_path)
            transcript = await run_in_threadpool(
                process_uploaded_audio,
                media_path,
                display_name,
                start_time=start_time,
                end_time=end_time,
            )
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        await file.close()

    return UploadAudioTranscriptResponse.model_validate(transcript)
