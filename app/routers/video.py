from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.schemas.video import UploadVideoSubtitleResponse
from app.services.errors import VideoExtractionError
from app.services.video import process_uploaded_video, safe_upload_name, store_upload


router = APIRouter(prefix="/api/video", tags=["video"])


@router.post(
    "/subtitle",
    response_model=UploadVideoSubtitleResponse,
    response_model_exclude_none=True,
)
async def video_subtitle(
    file: UploadFile = File(..., description="A local video file"),
) -> UploadVideoSubtitleResponse:
    try:
        display_name, suffix = safe_upload_name(file.filename)
        with TemporaryDirectory(
            prefix="tiger-you-vtt-upload-"
        ) as temporary_directory:
            working_directory = Path(temporary_directory)
            media_path = working_directory / f"upload{suffix}"
            await store_upload(file, media_path)
            subtitle = await run_in_threadpool(
                process_uploaded_video,
                media_path,
                display_name,
                working_directory,
            )
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        await file.close()

    return UploadVideoSubtitleResponse.model_validate(subtitle)
