from fastapi import FastAPI

from app.routers.audio import router as audio_router
from app.routers.jobs import router as jobs_router
from app.routers.microphone import router as microphone_router
from app.routers.overlay import router as overlay_router
from app.routers.system_audio import router as system_audio_router
from app.routers.ui import router as ui_router
from app.routers.video import router as video_router
from app.routers.youtube import router as youtube_router


app = FastAPI(title="YouTube Subtitle Extractor", version="1.0.0")
app.include_router(audio_router)
app.include_router(youtube_router)
app.include_router(video_router)
app.include_router(jobs_router)
app.include_router(system_audio_router)
app.include_router(microphone_router)
app.include_router(overlay_router)
app.include_router(ui_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}

