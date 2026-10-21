"""POST /connector/v1/{connector_key}/{method} - the one door IncuBrix's Connector Runtime calls."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.connectors.base import BaseConnector
from app.connectors.manifest import METHOD_CAPABILITY
from app.errors import ErrorCode, IncuBrixError
from app.models import (
    AssistPackRequest,
    DeleteRequest,
    MetricsRequest,
    PublishRequest,
    RefreshAuthRequest,
    StatusRequest,
    ValidateRequest,
)

router = APIRouter()

# method name -> (request model, how to call the connector with it)
_DISPATCH: dict[str, tuple[type[BaseModel], Callable[[BaseConnector, Any], BaseModel]]] = {
    "validate": (ValidateRequest, lambda c, r: c.validate(r.rendition, r.destination, r.connection)),
    "publish": (PublishRequest, lambda c, r: c.publish(r.rendition, r.destination, r.connection, r.idempotency_key)),
    "get_status": (StatusRequest, lambda c, r: c.get_status(r.platform_post_id, r.connection)),
    "delete": (DeleteRequest, lambda c, r: c.delete(r.platform_post_id, r.connection)),
    "fetch_metrics": (MetricsRequest, lambda c, r: c.fetch_metrics(r.platform_post_id, r.connection, r.since)),
    "refresh_auth": (RefreshAuthRequest, lambda c, r: c.refresh_auth(r.connection)),
    "build_assist_pack": (AssistPackRequest, lambda c, r: c.build_assist_pack(r.rendition, r.destination)),
}
assert set(_DISPATCH) == set(METHOD_CAPABILITY)


@router.post("/connector/v1/{connector_key}/{method}")
def call_connector(
    connector_key: str,
    method: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    if method not in _DISPATCH:
        raise IncuBrixError(ErrorCode.UNKNOWN_METHOD, f"no method {method!r}", {"methods": sorted(_DISPATCH)})
    connector = request.app.state.registry.get(connector_key)
    if not connector.manifest.supports(method):
        raise IncuBrixError(
            ErrorCode.UNKNOWN_METHOD,
            f"connector {connector_key!r} does not support {method!r}",
            {"capabilities": connector.manifest.capabilities},
        )
    model, call = _DISPATCH[method]
    if method == "publish" and "idempotency_key" not in body and idempotency_key:
        body = {**body, "idempotency_key": idempotency_key}  # the Idempotency-Key header works too
    try:
        parsed = model.model_validate(body)
    except ValidationError as exc:
        fields = [{"field": ".".join(str(p) for p in e["loc"]), "reason": e["msg"]} for e in exc.errors()]
        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "Request body is invalid", {"fields": fields}) from exc
    if method == "publish":  # publish goes through idempotency + rules, not straight to the connector
        result, replayed = request.app.state.publish_service.publish(connector_key, parsed)
        return JSONResponse(content=result, headers={"Idempotent-Replayed": "true"} if replayed else {})
    return JSONResponse(content=call(connector, parsed).model_dump(mode="json"))
