"""POST /api/v1/imports (upload a CSV, get a job) and downloads of the import files."""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from app.csv_import import import_id_for, parse_csv
from app.errors import ErrorCode, IncuBrixError

router = APIRouter(prefix="/api/v1")


@router.post("/imports", status_code=202)
async def create_import(request: Request, commit: bool = True, callback_url: str | None = None, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    """Body: the CSV itself (Content-Type text/csv) or JSON {"csv": "..."}. Columns: name, url (required); language, topics, connector_key, community (optional)."""
    raw = await request.body()
    if "json" in (request.headers.get("content-type") or ""):
        try:
            payload = json.loads(raw)
            text = payload.get("csv") if isinstance(payload, dict) else None
            if text is None and isinstance(payload, dict) and payload.get("csv_url"):
                text = request.app.state.importer.fetch_csv(payload["csv_url"])  # an address instead of an upload
        except ValueError:
            text = None
        if text is None:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, 'JSON body must look like {"csv": "name,url\\n..."} or {"csv_url": "https://..."}')
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "the CSV must be UTF-8 text") from None
    rows = len(parse_csv(text))  # refuse a malformed file now, with a clear message, not inside the job
    import_id = import_id_for(text)
    if request.app.state.imports.read(f"{import_id}/input.csv") is None:  # same content = same id: never rewrite a file a running job may be reading
        request.app.state.imports.save(import_id, "input.csv", text)
    job, created = request.app.state.jobs.submit("import", {"import_id": import_id, "commit": commit}, callback_url, idempotency_key)
    return JSONResponse(status_code=202, content={**job, "rows": rows, "import_id": import_id}, headers={} if created else {"Idempotent-Replayed": "true"})


@router.get("/import-files/{path:path}")
def download_import_file(path: str, request: Request) -> FileResponse:
    file = request.app.state.imports.read(path)
    if file is None:
        raise IncuBrixError(ErrorCode.NOT_FOUND, "no such import file")
    return FileResponse(file)
