"""Health and readiness endpoints (unauthenticated)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from ai_horde_service_alerts.clients.alertmanager import AlertmanagerClient
from ai_horde_service_alerts.clients.mimir import MimirClient
from ai_horde_service_alerts.db.session import DatabaseBundle
from ai_horde_service_alerts.deps import DependencyBundle


class HealthResponse(BaseModel):
    """Represents the response payload for `/healthz` and `/readyz`."""

    status: str
    upstreams: dict[str, str] = {}


def create_router(dependencies: DependencyBundle) -> APIRouter:
    """Create the health router with app-scoped service dependencies."""
    router = APIRouter(tags=["health"])

    AlertmanagerDep = Annotated[AlertmanagerClient, Depends(dependencies.get_alertmanager_client)]
    MimirDep = Annotated[MimirClient, Depends(dependencies.get_mimir_client)]
    DatabaseDep = Annotated[DatabaseBundle | None, Depends(dependencies.get_database)]

    @router.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        """Return liveness for the service process itself (no upstream probes)."""
        return HealthResponse(status="ok")

    @router.get("/readyz", response_model=HealthResponse)
    async def readyz(
        alertmanager: AlertmanagerDep,
        mimir: MimirDep,
        database: DatabaseDep,
        response: Response,
    ) -> HealthResponse:
        """Probe critical upstreams and return 503 when any are not ready."""
        am_ready = await alertmanager.is_ready()
        mimir_ready = await mimir.is_ready()
        db_ready = await _database_ready(database)
        upstreams = {
            "alertmanager": "ok" if am_ready else "unavailable",
            "mimir": "ok" if mimir_ready else "unavailable",
            "database": "ok" if db_ready else "unavailable",
        }
        if not (am_ready and mimir_ready and db_ready):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="degraded", upstreams=upstreams)
        return HealthResponse(status="ok", upstreams=upstreams)

    return router


async def _database_ready(database: DatabaseBundle | None) -> bool:
    if database is None:
        return False
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
