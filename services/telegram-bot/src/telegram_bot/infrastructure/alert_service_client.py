"""HTTP adapter for Alert Service's subscriber-cache invalidation endpoint.

Satisfies the IAlertServiceClient port. Mirrors RuleEngineClient.
"""
import httpx
import structlog

logger = structlog.get_logger(__name__)

_RELOAD_PATH = "/internal/reload-subscribers"
_TIMEOUT_SECONDS = 10.0


class AlertServiceClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def reload_subscribers(self) -> bool:
        url = f"{self._base_url}{_RELOAD_PATH}"
        try:
            resp = await self._client.post(url)
            ok = resp.status_code == 200
            logger.info("alert_service_reload", status_code=resp.status_code, ok=ok)
            return ok
        except httpx.HTTPError as exc:
            logger.warning("alert_service_reload_failed", error=str(exc))
            return False

    async def close(self) -> None:
        await self._client.aclose()
