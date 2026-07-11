"""Admin HTTP endpoints: health check + subscriber-cache invalidation.

Reads shared state via ``Depends(get_container)`` against
``request.app.state.container`` instead of module-level globals, so these
routes can be exercised with ``TestClient`` + ``app.dependency_overrides``
instead of monkeypatching ``main``'s module attributes.
"""
from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from alert_service.container import Container

router = APIRouter()


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


@router.post("/internal/reload-subscribers")
async def reload_subscribers(
    container: Container = Depends(get_container),
) -> JSONResponse:
    """Invalidate the subscriber cache.

    Called by the Telegram bot whenever it mutates ``user_preferences`` or
    ``user_watchlist`` so the next alert sees fresh routing data.
    """
    if container.cache is None:
        return JSONResponse(
            {"status": "noop", "reason": "fanout_disabled"},
            status_code=status.HTTP_409_CONFLICT,
        )
    container.cache.invalidate()
    return JSONResponse({"status": "ok", "stats": container.cache.stats})


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
