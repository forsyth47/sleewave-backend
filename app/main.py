from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.domain.models import (
    ApiError,
    DeviceDownloadConfirmationRequest,
    DeviceDownloadConfirmationResponse,
    DeviceLibrarySyncRequest,
    DeviceLibrarySyncResponse,
    ErrorResponse,
    PreparedTrackResponse,
    SearchResponse,
    TrackSelectionRequest,
)
from app.services.errors import MusicServiceError
from app.services.music_manager import MusicManager

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sleewave Backend",
    version="0.2.0",
    description="Local music aggregation backend for search, streaming, caching, and device downloads.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = MusicManager()


def _error_payload(error: ApiError) -> dict:
    payload = ErrorResponse(error=error)
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def _safe_file_name(title: str, artist: str) -> str:
    value = f"{artist} - {title}.mp3"
    sanitized = re.sub(r'[^A-Za-z0-9._ -]+', "", value).strip()
    return sanitized or "track.mp3"


@app.exception_handler(MusicServiceError)
async def music_service_error_handler(_: Request, exc: MusicServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            ApiError(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        ),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            ApiError(
                code="validation_error",
                message="The request payload is invalid.",
                details={"errors": exc.errors()},
            )
        ),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            ApiError(
                code="internal_server_error",
                message="An unexpected error occurred.",
            )
        ),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sources")
async def sources() -> dict[str, object]:
    return {"sources": manager.list_sources()}


@app.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    source: str | None = Query(default=None),
    sources: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    device_id: str | None = Query(default=None),
) -> SearchResponse:
    selector = sources or source or "all"
    return await manager.search(selector, q, limit, offset, device_id)


@app.post("/stream", response_model=PreparedTrackResponse)
async def stream(request: TrackSelectionRequest) -> PreparedTrackResponse:
    return await manager.prepare_track(request.result_id)


@app.post("/download", response_model=PreparedTrackResponse)
async def download(request: TrackSelectionRequest) -> PreparedTrackResponse:
    return await manager.prepare_track(request.result_id)


@app.post("/device-library/sync", response_model=DeviceLibrarySyncResponse)
async def sync_device_library(request: DeviceLibrarySyncRequest) -> DeviceLibrarySyncResponse:
    return manager.sync_device_library(request)


@app.post(
    "/device-library/confirm-download",
    response_model=DeviceDownloadConfirmationResponse,
)
async def confirm_device_download(
    request: DeviceDownloadConfirmationRequest,
) -> DeviceDownloadConfirmationResponse:
    return manager.confirm_device_download(request)


@app.get("/media/{cache_key}/stream")
async def stream_cached_track(cache_key: str) -> FileResponse:
    record = manager.get_cached_record(cache_key)
    file_name = _safe_file_name(record.title, record.artist)
    return FileResponse(
        record.file_path,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{file_name}"'},
    )


@app.get("/media/{cache_key}/download")
async def download_cached_track(cache_key: str) -> FileResponse:
    record = manager.get_cached_record(cache_key)
    file_name = _safe_file_name(record.title, record.artist)
    return FileResponse(
        record.file_path,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
