from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from app.schemas.youtube import (
    YouTubeInfoRequest,
    YouTubeSubtitleRequest,
    YouTubeSubtitleResponse,
    YouTubeSearchResponse,
    YouTubeVideoInfoResponse,
)
from app.services.youtube import VideoExtractionError, get_subtitle, get_video_info
from app.services.youtube_search import search_youtube


router = APIRouter(prefix="/api/youtube", tags=["youtube"])


@router.get("/search", response_model=YouTubeSearchResponse)
async def youtube_search(
    q: str,
    sort: str = "relevance",
    limit: int = 10,
) -> YouTubeSearchResponse:
    try:
        result = await run_in_threadpool(search_youtube, q, sort, limit)
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return YouTubeSearchResponse.model_validate(result)


@router.post("/info", response_model=YouTubeVideoInfoResponse)
async def youtube_info(request: YouTubeInfoRequest) -> YouTubeVideoInfoResponse:
    try:
        video_info = await run_in_threadpool(get_video_info, str(request.url))
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return YouTubeVideoInfoResponse.model_validate(video_info)


@router.post(
    "/subtitle",
    response_model=YouTubeSubtitleResponse,
    response_model_exclude_none=True,
)
async def youtube_subtitle(
    request: YouTubeSubtitleRequest,
) -> YouTubeSubtitleResponse:
    try:
        if (request.start_time or "").strip() or (
            request.end_time or ""
        ).strip():
            subtitle_arguments = {
                "start_time": request.start_time,
                "end_time": request.end_time,
            }
            if request.enable_diarization:
                subtitle_arguments["enable_diarization"] = True
            subtitle = await run_in_threadpool(
                get_subtitle,
                str(request.url),
                request.language,
                request.type,
                **subtitle_arguments,
            )
        else:
            if request.enable_diarization:
                subtitle = await run_in_threadpool(
                    get_subtitle,
                    str(request.url),
                    request.language,
                    request.type,
                    enable_diarization=True,
                )
            else:
                subtitle = await run_in_threadpool(
                    get_subtitle,
                    str(request.url),
                    request.language,
                    request.type,
                )
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return YouTubeSubtitleResponse.model_validate(subtitle)

