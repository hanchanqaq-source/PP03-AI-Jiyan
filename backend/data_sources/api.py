from __future__ import annotations

import json
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

import source_health

from .service import (
    DataSourceConflict,
    DataSourceRequestInvalid,
    DataSourceService,
    DataSourceUnavailable,
)


router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])
_service = DataSourceService()
_MAX_REQUEST_BODY = 8_192
_Model = TypeVar("_Model", bound=BaseModel)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    credential: SecretStr


class GlobalConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    free_only: bool


class AdapterConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    usage_mode: str | None = None
    daily_budget: str | None = None
    monthly_budget: str | None = None
    per_request_budget: str | None = None
    daily_request_limit: int | None = None
    monthly_request_limit: int | None = None


class AdapterActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm_paid_usage: bool = False


_PUBLIC_MESSAGES = {
    "license_required": "该数据源需要企业许可证",
    "catalog_only": "该数据源当前仅登记在目录中",
    "disabled": "该数据源当前不可启用",
    "unconfigured": "该数据源尚未配置凭据",
    "free_only": "Free-only 模式禁止启用付费数据源",
    "budget_required": "启用付费数据源前必须设置有效预算",
    "explicit_confirmation_required": "启用付费数据源需要明确确认",
    "unsupported_credential_transport": "当前凭据传输方式尚未通过安全核验",
    "configuration_barrier": "当前配置不允许该操作",
    "credential_not_supported": "该数据源不接受此凭据操作",
    "credential_source_read_only": "当前凭据来源为只读配置",
    "credential_store_unavailable": "凭据存储当前不可用",
    "configuration_store_unavailable": "数据源配置当前不可用",
    "usage_store_unavailable": "数据源用量记录当前不可用",
    "clock_unavailable": "服务时间当前不可用",
}


def _not_found() -> HTTPException:
    return HTTPException(404, "数据源目录项不存在")


class _DuplicateJsonField(ValueError):
    pass


def _exact_json_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    document: dict[object, object] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateJsonField("duplicate JSON field")
        document[key] = value
    return document


def _public_error(status_code: int, code: str) -> HTTPException:
    message = _PUBLIC_MESSAGES.get(code, "数据源请求无法完成")
    return HTTPException(status_code, {"code": code, "message": message})


def _call(callback):
    try:
        return callback()
    except KeyError as error:
        raise _not_found() from error
    except DataSourceRequestInvalid as error:
        raise _public_error(422, "invalid_request") from error
    except DataSourceConflict as error:
        raise _public_error(409, error.code) from error
    except DataSourceUnavailable as error:
        raise _public_error(503, error.code) from error


async def _request_model(request: Request, model_type: type[_Model], *, allow_empty: bool = False) -> _Model:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_REQUEST_BODY:
                raise _public_error(413, "request_too_large")
        except ValueError:
            raise _public_error(422, "invalid_request") from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_REQUEST_BODY:
            body.clear()
            raise _public_error(413, "request_too_large")
    if not body and allow_empty:
        document = {}
    else:
        try:
            document = json.loads(bytes(body), object_pairs_hook=_exact_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            body.clear()
            raise _public_error(422, "invalid_request") from None
    body.clear()
    if type(document) is not dict:
        raise _public_error(422, "invalid_request")
    try:
        return model_type.model_validate(document)
    except ValidationError:
        raise _public_error(422, "invalid_request") from None


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


@router.get("/config")
def config():
    return _call(_service.config_document)


@router.put("/config")
async def update_global_config(request: Request):
    model = await _request_model(request, GlobalConfigRequest)
    return _call(lambda: _service.update_free_only(model.free_only))


@router.put("/{adapter_id}/config")
async def update_adapter_config(request: Request, adapter_id: str = Path(min_length=1, max_length=160)):
    model = await _request_model(request, AdapterConfigRequest)
    updates = model.model_dump(exclude_unset=True)
    return _call(lambda: _service.update_adapter_config(adapter_id, updates))


@router.put("/{adapter_id}/credentials")
async def put_credentials(request: Request, adapter_id: str = Path(min_length=1, max_length=160)):
    model = await _request_model(request, CredentialRequest)
    value = model.credential.get_secret_value()
    try:
        return _call(lambda: _service.put_credential(adapter_id, value))
    finally:
        del value, model


@router.delete("/{adapter_id}/credentials")
def delete_credentials(adapter_id: str = Path(min_length=1, max_length=160)):
    return _call(lambda: _service.delete_credential(adapter_id))


@router.get("/usage")
def usage(adapter_id: str | None = Query(default=None, min_length=1, max_length=160)):
    return _call(lambda: _service.usage_document(adapter_id))


@router.get("/cost")
def cost(adapter_id: str | None = Query(default=None, min_length=1, max_length=160)):
    return _call(lambda: _service.cost_document(adapter_id))


@router.post("/refresh", status_code=202)
def refresh():
    return _refresh_or_conflict(_service.refresh)


@router.post("/{adapter_id}/{action}")
async def adapter_action(
    request: Request,
    adapter_id: str = Path(min_length=1, max_length=160),
    action: str = Path(pattern="^(enable|disable|validate)$"),
):
    model = await _request_model(request, AdapterActionRequest, allow_empty=True)
    if action == "enable":
        return _call(lambda: _service.enable_adapter(
            adapter_id, confirm_paid_usage=model.confirm_paid_usage,
        ))
    if action == "disable":
        if model.confirm_paid_usage:
            raise _public_error(422, "invalid_request")
        return _call(lambda: _service.disable_adapter(adapter_id))
    if model.confirm_paid_usage:
        raise _public_error(422, "invalid_request")
    return _call(lambda: _service.validate_adapter(adapter_id))
