"""HTTP adapter for the Rule Engine's hot-reload endpoint.

Satisfies the IRuleEngineClient port. The Rule Engine must be reached at
cfg.rule_engine_url — never hardcoded here.
"""
import httpx
import structlog

logger = structlog.get_logger(__name__)

_RELOAD_PATH = "/internal/reload-user-rules"
_TIMEOUT_SECONDS = 10.0


class RuleEngineClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def reload_user_rules(self) -> bool:
        """POST /internal/reload-user-rules. Returns True on 200, False otherwise."""
        url = f"{self._base_url}{_RELOAD_PATH}"
        try:
            resp = await self._client.post(url)
            ok = resp.status_code == 200
            logger.info("rule_engine_reload", status_code=resp.status_code, ok=ok)
            return ok
        except httpx.HTTPError as exc:
            logger.warning("rule_engine_reload_failed", error=str(exc))
            return False

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()
