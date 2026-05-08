from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ApiError(BaseModel):
    code: str
    message: str
    details: Optional[dict[str, Any]] = None


class ErrorResponse(BaseModel):
    error: ApiError


class SourceInfo(BaseModel):
    id: str
    name: str
    available: bool = True
    supports_search: bool = True
    supports_stream: bool = True
    supports_download: bool = True
    message: Optional[str] = None


class SourceWarning(BaseModel):
    source: str
    message: str


class TrackSourceRef(BaseModel):
    source: str
    track_id: str


class TrackAvailability(BaseModel):
    in_server_cache: bool = False
    on_device: bool = False
    cache_key: Optional[str] = None
    preferred_origin: str = "remote"


class Track(BaseModel):
    id: str
    title: str
    artist: str
    source: str
    duration: int = 0
    cover_url: Optional[str] = None
    stream_url: Optional[str] = None
    album: Optional[str] = None
    result_id: Optional[str] = None
    track_key: Optional[str] = None
    base_track_key: Optional[str] = None
    alternate_sources: list[TrackSourceRef] = Field(default_factory=list)
    availability: TrackAvailability = Field(default_factory=TrackAvailability)


class SearchResponse(BaseModel):
    query: str
    sources: list[str]
    results: list[Track]
    warnings: list[SourceWarning] = Field(default_factory=list)


class SearchTrackResult(BaseModel):
    title: str
    artist: str
    duration: int = 0
    cover_url: Optional[str] = None
    album: Optional[str] = None
    result_id: str
    track_key: str
    base_track_key: str
    availability: TrackAvailability


class SearchStreamEvent(BaseModel):
    event: str
    query: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    source: Optional[str] = None
    track: Optional[SearchTrackResult] = None
    warning: Optional[SourceWarning] = None
    emitted: Optional[int] = None


class CacheRecord(BaseModel):
    cache_key: str
    file_path: str
    file_size: int
    source: str
    track_id: str
    title: str
    artist: str
    duration: int = 0
    cover_url: Optional[str] = None
    album: Optional[str] = None
    track_key: str
    base_track_key: str
    created_at: datetime
    last_accessed_at: datetime


class SearchResultRecord(BaseModel):
    result_id: str
    track: Track
    created_at: datetime
    expires_at: datetime


class DeviceTrackRef(BaseModel):
    track_key: Optional[str] = None
    base_track_key: Optional[str] = None


class DeviceLibrarySyncRequest(BaseModel):
    device_id: str
    tracks: list[DeviceTrackRef]


class DeviceLibrarySyncResponse(BaseModel):
    device_id: str
    track_count: int
    server_cache_retained: bool = True


class DeviceDownloadConfirmationRequest(BaseModel):
    device_id: str
    result_id: Optional[str] = None
    track_key: Optional[str] = None
    base_track_key: Optional[str] = None


class DeviceDownloadConfirmationResponse(BaseModel):
    device_id: str
    registered: bool
    server_cache_retained: bool = True
