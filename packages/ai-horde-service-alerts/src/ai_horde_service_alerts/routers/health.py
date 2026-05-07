"""Health and readiness endpoints (unauthenticated)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.deps import DependencyBundle


class HealthResponse(BaseModel):
    """Represents the response payload for `/healthz` and `/readyz`."""

    status: str
    upstreams: dict[str, str] = {}


def create_router(dependencies: DependencyBundle) -> APIRouter:
    """Create the health router with app-scoped service dependencies."""
    router = APIRouter(tags=["health"])


    @router.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        """Return liveness for the service process itself (no upstream probes)."""
        return HealthResponse(status="ok")


    @router.get("/readyz", response_model=HealthResponse)
    async def readyz(alertmanager: Annotated[AlertmanagerClient, Depends(dependencies.get_alertmanager_client)], response: Response,) -> HealthResponse:
        """Probe critical upstreams and return 503 when any are not ready."""
        am_ready = await alertmanager.is_ready()
        upstreams = {"alertmanager": "ok" if am_ready else "unavailable"}
        if not am_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="degraded", upstreams=upstreams)
        return HealthResponse(status="ok", upstreams=upstreams)

    return router
