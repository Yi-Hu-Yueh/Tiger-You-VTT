from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["ui"])
_INDEX_PATH = Path(__file__).parents[1] / "static" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def application_page() -> HTMLResponse:
    return HTMLResponse(_INDEX_PATH.read_text(encoding="utf-8"))
