"""Peer-address based LAN protection. Never trust forwarded headers here."""
import hmac
import os
from ipaddress import ip_address

from starlette.responses import JSONResponse


def is_loopback(host: str) -> bool:
    try:
        address = ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


class MobileAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        peer = scope.get("client")
        if peer and is_loopback(peer[0]):
            return await self.app(scope, receive, send)
        # These existing endpoints independently reject every non-loopback peer.
        if scope.get("path") in {"/api/overlay/start", "/api/overlay/status"}:
            return await self.app(scope, receive, send)
        configured = os.environ.get("TIGER_MOBILE_TOKEN", "").encode("utf-8")
        supplied = [value for key, value in scope.get("headers", [])
                    if key.lower() == b"x-tiger-mobile-token"]
        if len(configured) < 32 or len(supplied) != 1 or not hmac.compare_digest(configured, supplied[0]):
            response = JSONResponse(status_code=401, content={"detail": {
                "code": "mobile_unauthorized", "message": "需要有效的行動連線金鑰。"}})
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
