from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse

from app.domain.models import (
    ApiError,
    CacheClearResponse,
    DeviceDownloadConfirmationRequest,
    DeviceDownloadConfirmationResponse,
    DeviceLibrarySyncRequest,
    DeviceLibrarySyncResponse,
    ErrorResponse,
    SavedSongsResponse,
    SearchStreamEvent,
    TrackDeleteResponse,
)
from app.services.errors import MusicServiceError
from app.services.music_manager import MusicManager

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sleewave Backend",
    version="0.3.0",
    description="Local music aggregation backend for search, streaming, caching, and device downloads.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize manager with optional redirect_to_original_url flag from environment
redirect_to_original = os.getenv("SLEEWAVE_REDIRECT_TO_ORIGINAL_URL", "false").lower() == "true"
manager = MusicManager(redirect_to_original_url=redirect_to_original)


def _error_payload(error: ApiError) -> dict:
    payload = ErrorResponse(error=error)
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def _safe_file_name(title: str, artist: str) -> str:
    value = f"{artist} - {title}.mp3"
    sanitized = re.sub(r'[^A-Za-z0-9._ -]+', "", value).strip()
    return sanitized or "track.mp3"


def _model_json(model) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json(exclude_none=True, exclude_defaults=True)
    if hasattr(model, "json"):
        return model.json(exclude_none=True, exclude_defaults=True)
    if hasattr(model, "model_dump"):
        return json.dumps(model.model_dump(exclude_none=True, exclude_defaults=True))
    return json.dumps(model.dict(exclude_none=True, exclude_defaults=True))

def _sse(event: SearchStreamEvent) -> str:
    return f"event: {event.event}\ndata: {_model_json(event)}\n\n"


async def _search_event_stream(
    selector: str,
    query: str,
    limit: int,
    offset: int,
    device_id: Optional[str],
) -> AsyncIterator[str]:
    async for event in manager.stream_search(selector, query, limit, offset, device_id):
        yield _sse(event)


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


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sources")
async def sources() -> dict[str, object]:
    return {"sources": manager.list_sources()}


@app.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    source: Optional[str] = Query(default=None),
    sources: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    device_id: Optional[str] = Query(default=None),
) -> StreamingResponse:
    selector = sources or source or "all"
    manager.prepare_search_request(selector, q, limit, offset)
    return StreamingResponse(
        _search_event_stream(selector, q, limit, offset, device_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/stream/{result_id}",
    response_class=FileResponse,
)
async def stream(result_id: str):
    return await _stream_result(result_id)


async def _stream_result(result_id: str):
    record, _ = await manager.prepare_cached_track(result_id)
    file_name = _safe_file_name(record.title, record.artist)

    # If file_path is a remote URL (starts with http), redirect to it
    if record.file_path.startswith("http"):
        return RedirectResponse(url=record.file_path)

    # Otherwise, serve as a local file
    return FileResponse(
        record.file_path,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{file_name}"'},
    )


@app.get(
    "/download/{result_id}",
    response_class=FileResponse,
)
async def download(
    result_id: str,
    device_id: Optional[str] = Query(default=None),
):
    return await _download_result(result_id, device_id)


async def _download_result(result_id: str, device_id: Optional[str]):
    record, _ = await manager.prepare_cached_track(
        result_id,
        device_id=device_id,
        block_device_duplicate=True,
    )
    file_name = _safe_file_name(record.title, record.artist)

    # If file_path is a remote URL (starts with http), redirect to it
    if record.file_path.startswith("http"):
        return RedirectResponse(url=record.file_path)

    # Otherwise, serve as a local file
    return FileResponse(
        record.file_path,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.get("/saved-songs", response_model=SavedSongsResponse)
async def saved_songs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SavedSongsResponse:
    return manager.list_saved_songs(limit=limit, offset=offset)


@app.delete("/tracks/{result_id}", response_model=TrackDeleteResponse)
async def delete_track(result_id: str) -> TrackDeleteResponse:
    return manager.delete_track(result_id)


@app.delete("/cache", response_model=CacheClearResponse)
async def clear_cache() -> CacheClearResponse:
    return manager.clear_cache()


@app.delete("/server-temp", response_model=CacheClearResponse)
async def clear_server_temp_storage() -> CacheClearResponse:
    return manager.clear_server_temp_storage()


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
