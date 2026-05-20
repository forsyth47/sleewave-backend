from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.domain.models import (
    CacheRecord,
    CacheClearResponse,
    DeviceDownloadConfirmationRequest,
    DeviceDownloadConfirmationResponse,
    DeviceLibrarySyncRequest,
    DeviceLibrarySyncResponse,
    DeviceTrackRef,
    SavedSongsResponse,
    SearchResponse,
    SearchStreamEvent,
    SearchTrackResult,
    SourceInfo,
    SourceWarning,
    Track,
    TrackAvailability,
    TrackSourceRef,
    TrackDeleteResponse,
)
from app.interfaces.music_provider import IMusicProvider
from app.providers.soundcloud import SoundCloudProvider
from app.providers.vk import VKProvider
from app.providers.youtube import YouTubeProvider
from app.providers.ytmusic import YTMusicProvider
from app.services.device_library import DeviceLibraryService
from app.services.errors import (
    BadRequestError,
    ProviderNotFoundError,
    ProviderUnavailableError,
    SearchResultNotFoundError,
    TrackAlreadyOnDeviceError,
)
from app.services.media_cache import MediaCacheService
from app.services.song_catalog import SongCatalog
from app.services.track_identity import (
    hydrate_device_track_keys,
    hydrate_track_keys,
    normalize_text,
    tracks_match,
)


logger = logging.getLogger(__name__)


@dataclass
class ProviderEntry:
    info: SourceInfo
    provider: Optional[IMusicProvider]
    priority: int
    cache_direct_links_in_background: bool = True


class MusicManager:
    def __init__(self) -> None:
        cache_root = Path(
            os.getenv(
                "SLEEWAVE_CACHE_DIR",
                str(Path(tempfile.gettempdir()) / "sleewave-media-cache"),
            )
        )
        # Ensure the cache directory exists at initialization
        cache_path = Path(cache_root)
        if not cache_path.exists():
            cache_path.mkdir(parents=True, exist_ok=True)

        max_cache_mb = int(os.getenv("SLEEWAVE_CACHE_MAX_MB", "1024"))
        self.cache = MediaCacheService(
            cache_root,
            max_size_bytes=max_cache_mb * 1024 * 1024,
        )
        self.device_library = DeviceLibraryService(cache_root / "device_library.json")
        self.song_catalog = SongCatalog(cache_root / "song_catalog.json")
        self._background_cache_tasks: dict[str, asyncio.Task[None]] = {}
        self._providers: dict[str, ProviderEntry] = {
            "ytm": ProviderEntry(
                info=SourceInfo(id="ytm", name="YouTube Music"),
                provider=YTMusicProvider(),
                priority=10,
                cache_direct_links_in_background=True,
            ),
            "yt": ProviderEntry(
                info=SourceInfo(id="yt", name="YouTube"),
                provider=YouTubeProvider(),
                priority=20,
                cache_direct_links_in_background=True,
            ),
            "sc": ProviderEntry(
                info=SourceInfo(id="sc", name="SoundCloud"),
                provider=SoundCloudProvider(),
                priority=30,
                cache_direct_links_in_background=True,
            ),
            "spotify": ProviderEntry(
                info=SourceInfo(
                    id="spotify",
                    name="Spotify",
                    available=False,
                    message="Spotify integration has not been implemented yet.",
                ),
                provider=None,
                priority=40,
                cache_direct_links_in_background=False,
            ),
            "vk": ProviderEntry(
                info=SourceInfo(id="vk", name="VK Music"),
                provider=VKProvider(),
                priority=50,
                cache_direct_links_in_background=True,
            ),
        }

    def list_sources(self) -> list[SourceInfo]:
        return [entry.info for _, entry in sorted(self._providers.items(), key=lambda item: item[1].priority)]

    async def search(
        self,
        source_selector: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        device_id: Optional[str] = None,
    ) -> SearchResponse:
        cleaned_query, source_ids, fetch_limit = self.prepare_search_request(
            source_selector,
            query,
            limit,
            offset,
        )

        results = await asyncio.gather(
            *(self._search_source(source_id, cleaned_query, fetch_limit) for source_id in source_ids),
            return_exceptions=True,
        )

        warnings: list[SourceWarning] = []
        merged_tracks: list[Track] = []

        for source_id, result in zip(source_ids, results):
            if isinstance(result, Exception):
                if len(source_ids) == 1:
                    raise ProviderUnavailableError(
                        source_id,
                        f"Search failed for source '{source_id}'.",
                    ) from result
                warnings.append(
                    SourceWarning(
                        source=source_id,
                        message=f"Search failed for source '{source_id}' and it was skipped.",
                    )
                )
                continue
            merged_tracks.extend(result)

        deduplicated = self._deduplicate_tracks(merged_tracks)
        self._annotate_availability(deduplicated, device_id)
        ordered = sorted(deduplicated, key=self._search_sort_key)
        paged_results = ordered[offset : offset + limit]
        paged_results = self.song_catalog.upsert_tracks(paged_results)
        return SearchResponse(
            query=cleaned_query,
            sources=source_ids,
            results=paged_results,
            warnings=warnings,
        )

    async def stream_search(
        self,
        source_selector: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        device_id: Optional[str] = None,
    ):
        cleaned_query, source_ids, fetch_limit = self.prepare_search_request(
            source_selector,
            query,
            limit,
            offset,
        )
        emitted = 0
        seen = 0
        merged_tracks: list[Track] = []

        yield SearchStreamEvent(
            event="start",
            query=cleaned_query,
            sources=source_ids,
            emitted=0,
        )

        for track in self._cached_tracks_for_query(cleaned_query):
            if self._contains_duplicate(merged_tracks, track):
                continue
            self._annotate_availability([track], device_id)
            merged_tracks.append(track)
            seen += 1
            if seen <= offset:
                continue
            if emitted >= limit:
                continue
            stored_track = self.song_catalog.upsert_track(track)
            emitted += 1
            yield SearchStreamEvent(
                event="track",
                source=track.source,
                track=self._to_search_track_result(stored_track),
                emitted=emitted,
            )

        if emitted >= limit:
            yield SearchStreamEvent(
                event="done",
                emitted=emitted,
            )
            return

        async def fetch_source(source_id: str) -> tuple[str, list[Track], Optional[Exception]]:
            try:
                return source_id, await self._search_source(source_id, cleaned_query, fetch_limit), None
            except Exception as exc:
                return source_id, [], exc

        tasks = [asyncio.create_task(fetch_source(source_id)) for source_id in source_ids]
        try:
            for task in asyncio.as_completed(tasks):
                source_id, tracks, error = await task
                if error:
                    if len(source_ids) == 1:
                        raise ProviderUnavailableError(
                            source_id,
                            f"Search failed for source '{source_id}'.",
                        ) from error
                    yield SearchStreamEvent(
                        event="warning",
                        source=source_id,
                        warning=SourceWarning(
                            source=source_id,
                            message=f"Search failed for source '{source_id}' and it was skipped.",
                        ),
                        emitted=emitted,
                    )
                    continue

                for track in tracks:
                    hydrated = hydrate_track_keys(track)
                    if self._contains_duplicate(merged_tracks, hydrated):
                        continue
                    self._annotate_availability([hydrated], device_id)
                    merged_tracks.append(hydrated)
                    seen += 1
                    if seen <= offset:
                        continue
                    if emitted >= limit:
                        continue
                    stored_track = self.song_catalog.upsert_track(hydrated)
                    emitted += 1
                    yield SearchStreamEvent(
                        event="track",
                        source=source_id,
                        track=self._to_search_track_result(stored_track),
                        emitted=emitted,
                    )

                if emitted >= limit:
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

        yield SearchStreamEvent(
            event="done",
            emitted=emitted,
        )

    def prepare_search_request(
        self,
        source_selector: str,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[str, list[str], int]:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise BadRequestError("The search query cannot be empty.")
        if limit < 1 or limit > 100:
            raise BadRequestError("The limit must be between 1 and 100.")
        if offset < 0:
            raise BadRequestError("The offset cannot be negative.")

        source_ids = self._resolve_sources(source_selector)
        fetch_limit = min(limit + offset + 10, 100)
        return cleaned_query, source_ids, fetch_limit

    async def prepare_cached_track(
        self,
        result_id: str,
        *,
        device_id: Optional[str] = None,
        block_device_duplicate: bool = False,
        direct_url: bool = False,
    ) -> tuple[CacheRecord, bool]:
        try:
            track = hydrate_track_keys(self.song_catalog.get_track(result_id))
        except SearchResultNotFoundError:
            record = self.cache.get_by_cache_key(result_id)
            track = self.song_catalog.upsert_track(self._track_from_cache_record(record))

        if block_device_duplicate and device_id and self.device_library.has_track(device_id, track):
            raise TrackAlreadyOnDeviceError(device_id, track.result_id or track.track_key)

        cached_record = self.cache.find_by_track(track)
        if cached_record:
            self.cache.touch(cached_record.cache_key)
            return cached_record, True

        if direct_url:
            direct_stream_url = await self._resolve_direct_stream_url(track)
            if direct_stream_url:
                if self._should_cache_direct_link_in_background(track.source):
                    self._schedule_background_cache(track, stream_url=direct_stream_url)
                return self._build_remote_cache_record(track, direct_stream_url), False

        async def downloader(target_path: str) -> Optional[str]:
            provider = self._get_download_provider(track.source)
            return await provider.download(track.id, target_path)

        return await self.cache.ensure_cached(track, downloader)

    def sync_device_library(self, request: DeviceLibrarySyncRequest) -> DeviceLibrarySyncResponse:
        tracks = [self._device_track_from_result_id(track.result_id) for track in request.tracks]
        track_count = self.device_library.replace_tracks(request.device_id, tracks)
        return DeviceLibrarySyncResponse(
            device_id=request.device_id,
            track_count=track_count,
            server_cache_retained=True,
        )

    def confirm_device_download(
        self,
        request: DeviceDownloadConfirmationRequest,
    ) -> DeviceDownloadConfirmationResponse:
        request_track = self._device_track_from_result_id(request.result_id)
        hydrated_request_track = hydrate_device_track_keys(request_track)
        registered = self.device_library.add_track(request.device_id, hydrated_request_track)
        return DeviceDownloadConfirmationResponse(
            device_id=request.device_id,
            registered=registered,
            server_cache_retained=True,
        )

    def list_saved_songs(self, *, limit: int = 50, offset: int = 0) -> SavedSongsResponse:
        self._validate_pagination(limit, offset, max_limit=200)
        records = self.cache.list_records()
        paged_records = records[offset : offset + limit]
        tracks = self.song_catalog.upsert_tracks(
            [self._track_from_cache_record(record) for record in paged_records]
        )
        songs = [
            self._to_search_track_result(track)
            for track in tracks
        ]
        total = len(records)
        return SavedSongsResponse(
            songs=songs,
            count=len(songs),
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(songs) < total,
        )

    def delete_track(self, result_id: str) -> TrackDeleteResponse:
        cache_deleted = False
        try:
            track = self.song_catalog.get_track(result_id)
            cache_deleted = self.cache.delete_by_track(track)
            self.song_catalog.delete_track(result_id)
        except SearchResultNotFoundError:
            cache_deleted = self.cache.delete(result_id)
            if not cache_deleted:
                raise

        return TrackDeleteResponse(
            result_id=result_id,
            cache_deleted=cache_deleted,
        )

    def clear_cache(self) -> CacheClearResponse:
        return CacheClearResponse(deleted_count=self.cache.clear())

    def clear_server_temp_storage(self) -> CacheClearResponse:
        deleted_count = self.cache.clear()
        self.song_catalog.clear()
        self.device_library.clear()
        return CacheClearResponse(
            deleted_count=deleted_count,
            track_catalog_cleared=True,
            device_library_cleared=True,
        )

    def get_cached_record(self, cache_key: str):
        self.cache.touch(cache_key)
        return self.cache.get_by_cache_key(cache_key)

    def _build_remote_cache_record(self, track: Track, direct_url: str) -> CacheRecord:
        now = datetime.now(timezone.utc)
        return CacheRecord(
            cache_key=track.track_key or track.result_id or track.id,
            file_path=direct_url,
            file_size=0,
            source=track.source,
            track_id=track.id,
            title=track.title,
            artist=track.artist,
            duration=track.duration or 0,
            cover_url=track.cover_url,
            album=track.album,
            track_key=track.track_key or "",
            base_track_key=track.base_track_key or "",
            created_at=now,
            last_accessed_at=now,
        )

    def _schedule_background_cache(self, track: Track, stream_url: Optional[str] = None) -> None:
        cache_key = track.track_key or track.result_id or track.id
        existing = self._background_cache_tasks.get(cache_key)
        if existing and not existing.done():
            return

        async def warm_cache() -> None:
            try:
                provider = self._get_download_provider(track.source)

                async def downloader(target_path: str) -> Optional[str]:
                    return await provider.download(track.id, target_path, stream_url=stream_url)

                await self.cache.ensure_cached(track, downloader)
            except Exception as exc:
                logger.warning("Background cache warm failed for %s: %s", cache_key, exc)
            finally:
                self._background_cache_tasks.pop(cache_key, None)

        self._background_cache_tasks[cache_key] = asyncio.create_task(warm_cache())

    async def _resolve_direct_stream_url(self, track: Track) -> Optional[str]:
        try:
            provider = self._get_download_provider(track.source)
            direct_url = await provider.get_stream(track.id)
            if isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
                return direct_url
            return None
        except Exception as exc:
            logger.warning(
                "Could not resolve direct stream URL for %s:%s: %s",
                track.source,
                track.id,
                exc,
            )
            return None

    def _should_cache_direct_link_in_background(self, source_id: str) -> bool:
        entry = self._providers.get(source_id)
        return bool(entry and entry.cache_direct_links_in_background)

    async def _search_source(self, source_id: str, query: str, limit: int) -> list[Track]:
        entry = self._providers[source_id]
        if not entry.info.available:
            raise ProviderUnavailableError(
                source_id,
                entry.info.message or f"Source '{source_id}' is unavailable.",
            )
        if entry.provider is None:
            raise ProviderUnavailableError(source_id, f"Source '{source_id}' has no provider.")
        return await entry.provider.search(query, limit=limit, offset=0)

    def _resolve_sources(self, source_selector: str) -> list[str]:
        requested = [item.strip().lower() for item in source_selector.split(",") if item.strip()]
        if not requested or requested == ["all"]:
            return [
                source_id
                for source_id, entry in sorted(self._providers.items(), key=lambda item: item[1].priority)
                if entry.info.available
            ]

        resolved = []
        seen = set()
        for source_id in requested:
            if source_id in seen:
                continue
            if source_id not in self._providers:
                raise ProviderNotFoundError(source_id)
            entry = self._providers[source_id]
            if not entry.info.available:
                raise ProviderUnavailableError(
                    source_id,
                    entry.info.message or f"Source '{source_id}' is unavailable.",
                )
            resolved.append(source_id)
            seen.add(source_id)
        return resolved

    def _validate_pagination(self, limit: int, offset: int, *, max_limit: int) -> None:
        if limit < 1 or limit > max_limit:
            raise BadRequestError(f"The limit must be between 1 and {max_limit}.")
        if offset < 0:
            raise BadRequestError("The offset cannot be negative.")

    def _get_download_provider(self, source_id: str) -> IMusicProvider:
        entry = self._providers.get(source_id)
        if not entry:
            raise ProviderNotFoundError(source_id)
        if not entry.info.available or entry.provider is None:
            raise ProviderUnavailableError(
                source_id,
                entry.info.message or f"Source '{source_id}' is unavailable.",
            )
        return entry.provider

    def _deduplicate_tracks(self, tracks: list[Track]) -> list[Track]:
        buckets: dict[str, list[Track]] = {}
        for track in tracks:
            hydrated = hydrate_track_keys(track)
            bucket = buckets.setdefault(hydrated.base_track_key or "", [])
            merged = False
            for existing in bucket:
                if tracks_match(existing, hydrated):
                    self._merge_track(existing, hydrated)
                    merged = True
                    break
            if not merged:
                bucket.append(hydrated)

        results: list[Track] = []
        for grouped_tracks in buckets.values():
            results.extend(grouped_tracks)
        return results

    def _merge_if_duplicate(self, tracks: list[Track], incoming: Track) -> bool:
        for existing in tracks:
            if tracks_match(existing, incoming):
                self._merge_track(existing, incoming)
                return True
        return False

    def _contains_duplicate(self, tracks: list[Track], incoming: Track) -> bool:
        return any(tracks_match(existing, incoming) for existing in tracks)

    def _to_search_track_result(self, track: Track) -> SearchTrackResult:
        availability = TrackAvailability(
            in_server_cache=track.availability.in_server_cache,
            on_device=track.availability.on_device,
            cache_key=track.availability.cache_key,
            preferred_origin=track.availability.preferred_origin,
        )
        return SearchTrackResult(
            title=track.title,
            artist=track.artist,
            duration=track.duration,
            cover_url=track.cover_url,
            album=track.album,
            result_id=track.result_id or "",
            availability=availability,
        )

    def _cached_tracks_for_query(self, query: str) -> list[Track]:
        normalized_query = normalize_text(query)
        if not normalized_query:
            return []

        tracks = []
        for record in self.cache.list_records():
            haystack = normalize_text(
                " ".join(
                    item
                    for item in [record.title, record.artist, record.album]
                    if item
                )
            )
            if normalized_query in haystack:
                tracks.append(self._track_from_cache_record(record))
        return tracks

    def _track_from_cache_record(self, record: CacheRecord) -> Track:
        return Track(
            id=record.track_id,
            title=record.title,
            artist=record.artist,
            source=record.source,
            duration=record.duration,
            cover_url=record.cover_url,
            album=record.album,
            track_key=record.track_key,
            base_track_key=record.base_track_key,
            availability=TrackAvailability(
                in_server_cache=True,
                cache_key=record.cache_key,
                preferred_origin="server",
            ),
        )

    def _merge_track(self, current: Track, incoming: Track) -> None:
        existing_refs = {(ref.source, ref.track_id) for ref in current.alternate_sources}
        existing_refs.add((current.source, current.id))
        incoming_ref = (incoming.source, incoming.id)
        if incoming_ref not in existing_refs:
            current.alternate_sources.append(
                TrackSourceRef(source=incoming.source, track_id=incoming.id)
            )

        if self._provider_priority(incoming.source) < self._provider_priority(current.source):
            previous_primary = TrackSourceRef(source=current.source, track_id=current.id)
            current.id = incoming.id
            current.source = incoming.source
            current.title = incoming.title or current.title
            current.artist = incoming.artist or current.artist
            current.duration = incoming.duration or current.duration
            current.cover_url = incoming.cover_url or current.cover_url
            current.album = incoming.album or current.album
            current.alternate_sources = [
                ref
                for ref in current.alternate_sources
                if (ref.source, ref.track_id) != (current.source, current.id)
            ]
            if (previous_primary.source, previous_primary.track_id) != (current.source, current.id):
                current.alternate_sources.append(previous_primary)
        else:
            if not current.cover_url:
                current.cover_url = incoming.cover_url
            if not current.album:
                current.album = incoming.album
            if not current.duration:
                current.duration = incoming.duration

        deduped_refs = []
        seen_refs = {(current.source, current.id)}
        for ref in current.alternate_sources:
            key = (ref.source, ref.track_id)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            deduped_refs.append(ref)
        current.alternate_sources = deduped_refs

    def _annotate_availability(self, tracks: list[Track], device_id: Optional[str]) -> None:
        cache_records = self.cache.find_by_tracks(tracks)
        for index, track in enumerate(tracks):
            cache_record = cache_records.get(index)
            on_device = self.device_library.has_track(device_id, track) if device_id else False
            preferred_origin = "remote"
            cache_key = None
            in_server_cache = False

            if cache_record:
                cache_key = cache_record.cache_key
                in_server_cache = True
                preferred_origin = "server"
            if on_device:
                preferred_origin = "device"

            track.availability = TrackAvailability(
                in_server_cache=in_server_cache,
                on_device=on_device,
                cache_key=cache_key,
                preferred_origin=preferred_origin,
            )

    def _search_sort_key(self, track: Track) -> tuple[int, int, str, str]:
        if track.availability.on_device:
            availability_rank = 0
        elif track.availability.in_server_cache:
            availability_rank = 1
        else:
            availability_rank = 2

        return (
            availability_rank,
            self._provider_priority(track.source),
            track.artist.lower(),
            track.title.lower(),
        )

    def _provider_priority(self, source_id: str) -> int:
        entry = self._providers.get(source_id)
        return entry.priority if entry else 1000

    def _device_track_from_result_id(self, result_id: str) -> DeviceTrackRef:
        try:
            track = hydrate_track_keys(self.song_catalog.get_track(result_id))
            return DeviceTrackRef(
                track_key=track.track_key or result_id,
                base_track_key=track.base_track_key,
            )
        except SearchResultNotFoundError:
            return DeviceTrackRef(
                track_key=result_id,
                base_track_key=None,
            )
