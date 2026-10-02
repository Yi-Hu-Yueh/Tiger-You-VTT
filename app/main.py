from fastapi import FastAPI

from app.routers.youtube import router as youtube_router


app = FastAPI(title="YouTube Subtitle Extractor", version="1.0.0")
app.include_router(youtube_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}

