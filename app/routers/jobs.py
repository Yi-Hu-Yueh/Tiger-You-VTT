from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.jobs import JobStartResponse, JobStatusResponse
from app.schemas.youtube import YouTubeInfoRequest
from app.services.errors import VideoExtractionError
from app.services.jobs import JOB_MANAGER, JobNotFoundError
from app.services.video import safe_upload_name, store_upload


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "job_not_found",
            "message": "The requested background job does not exist.",
        },
    )


@router.post("/video", response_model=JobStartResponse, status_code=202)
async def create_video_job(
    file: UploadFile = File(..., description="A local video file"),
) -> JobStartResponse:
    temporary_directory: TemporaryDirectory[str] | None = None
    try:
        display_name, suffix = safe_upload_name(file.filename)
        temporary_directory = TemporaryDirectory(
            prefix="tiger-you-vtt-job-"
        )
        media_path = Path(temporary_directory.name) / f"upload{suffix}"
        await store_upload(file, media_path)
        started = JOB_MANAGER.create_video_job(
            temporary_directory, media_path, display_name
        )
        temporary_directory = None
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        await file.close()
        if temporary_directory is not None:
            temporary_directory.cleanup()

    return JobStartResponse.model_validate(started)


@router.post("/youtube", response_model=JobStartResponse, status_code=202)
def create_youtube_job(request: YouTubeInfoRequest) -> JobStartResponse:
    started = JOB_MANAGER.create_youtube_job(str(request.url))
    return JobStartResponse.model_validate(started)


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    response_model_exclude_none=True,
)
def get_job(job_id: str) -> JobStatusResponse:
    try:
        snapshot = JOB_MANAGER.snapshot(job_id)
    except JobNotFoundError as exc:
        raise _not_found() from exc
    return JobStatusResponse.model_validate(snapshot)


@router.post(
    "/{job_id}/stop",
    response_model=JobStatusResponse,
    response_model_exclude_none=True,
)
def stop_job(job_id: str) -> JobStatusResponse:
    try:
        snapshot = JOB_MANAGER.stop(job_id)
    except JobNotFoundError as exc:
        raise _not_found() from exc
    return JobStatusResponse.model_validate(snapshot)
