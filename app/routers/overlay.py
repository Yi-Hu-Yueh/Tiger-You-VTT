"""Localhost-only endpoints for launching the native caption overlay."""

from __future__ import annotations

from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.services.overlay_launcher import OverlayLaunchError, OverlayLauncher


router = APIRouter(prefix="/api/overlay", tags=["overlay"])
overlay_launcher = OverlayLauncher()


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    try:
        address = ip_address(host.split("%", 1)[0])
    except ValueError:
        address = None
    mapped = getattr(address, "ipv4_mapped", None)
    if address is None or not (
        address.is_loopback or (mapped is not None and mapped.is_loopback)
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "overlay_localhost_required",
                "message": "桌面字幕 Overlay 只能從本機啟動。",
            },
        )


@router.get("/status")
def overlay_status(request: Request) -> dict[str, bool]:
    _require_loopback(request)
    return overlay_launcher.status()


@router.post("/start")
async def start_overlay(request: Request) -> dict[str, bool | str]:
    _require_loopback(request)
    # A remote website can submit a request to localhost from a local browser.
    # Reject that case as well as direct non-loopback callers.
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(
            status_code=403,
            detail={"code": "overlay_origin_rejected", "message": "請從本機 Tiger-You-VTT 網頁啟動 Overlay。"},
        )
    if request.query_params or (await request.body()).strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "overlay_arguments_not_allowed",
                "message": "桌面字幕 Overlay 啟動不接受自訂參數。",
            },
        )
    try:
        return await run_in_threadpool(overlay_launcher.start)
    except OverlayLaunchError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
