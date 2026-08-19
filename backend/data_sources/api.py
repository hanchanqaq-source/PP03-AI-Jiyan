from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

import source_health

from .service import DataSourceService


router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])
_service = DataSourceService()


def _not_found() -> HTTPException:
    return HTTPException(404, "数据源目录项不存在")


def _refresh_or_conflict(callback):
    try:
        return callback()
    except source_health.FullRunConflict as error:
        # Never pass a probe/runtime error into the public response.
        raise HTTPException(409, "数据源体检正在运行") from error


@router.get("/catalog")
def catalog():
    return _service.catalog_document()


@router.get("/families")
def families():
    return _service.families_document()


@router.get("/families/{family_id}")
def family(family_id: str = Path(min_length=1, max_length=160)):
    try:
        return _service.family_document(family_id)
    except KeyError as error:
        raise _not_found() from error


@router.get("/capabilities")
def capabilities():
    return _service.capabilities_document()


@router.post("/refresh", status_code=202)
def refresh():
    return _refresh_or_conflict(_service.refresh)


@router.post("/{adapter_id}/{action}")
def adapter_action(
    adapter_id: str = Path(min_length=1, max_length=160),
    action: str = Path(pattern="^(enable|disable|validate)$"),
):
    try:
        if action == "validate":
            return _refresh_or_conflict(lambda: _service.adapter_action(adapter_id, action))
        return _service.adapter_action(adapter_id, action)
    except KeyError as error:
        raise _not_found() from error
