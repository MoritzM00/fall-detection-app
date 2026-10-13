"""GPU serving status, warm-up and shutdown routes, one status per served model."""

import subprocess
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from fall_detection.config import Settings
from fall_detection.serving_status import ServingStatus, ShutdownBlockedError, StatusSnapshot

# Omitted means the default model, so older clients keep working.
ModelQuery = Annotated[str | None, Query(min_length=1)]


def serving_router(settings: Settings) -> APIRouter:
    """Build status routes; each model runs on its own Modal function and endpoint."""
    router = APIRouter()
    statuses = {
        endpoint.model: ServingStatus(settings, endpoint=endpoint)
        for endpoint in settings.endpoints
    }

    def status_for(model: str | None) -> ServingStatus:
        try:
            return statuses[model or settings.inference_model]
        except KeyError:
            raise HTTPException(404, "Choose a model advertised by capabilities") from None

    @router.get("/serving-status")
    def get_serving_status(model: ModelQuery = None) -> StatusSnapshot:
        """Report GPU container state from the control plane without waking the GPU."""
        return status_for(model).snapshot()

    @router.post("/serving-status/warmup", status_code=status.HTTP_202_ACCEPTED)
    def warm_up_serving(model: ModelQuery = None) -> StatusSnapshot:
        """Wake one model's GPU endpoint in the background so a session starts warm."""
        try:
            return status_for(model).start_warmup()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/serving-status/shutdown")
    def shut_down_serving(model: ModelQuery = None) -> StatusSnapshot:
        """Stop every GPU container of the app; the next request starts a new one."""
        current = status_for(model)
        others = tuple(item for item in statuses.values() if item is not current)
        try:
            return current.shutdown(others)
        except (ValueError, ShutdownBlockedError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=502, detail=f"Modal CLI failed: {exc}") from exc

    return router
