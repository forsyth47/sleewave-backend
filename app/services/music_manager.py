from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.domain.models import (
    DeviceDownloadConfirmationRequest,
    DeviceDownloadConfirmationResponse,
    DeviceLibrarySyncRequest,
    DeviceLibrarySyncResponse,
    DeviceLibraryTrack,
    PreparedTrackResponse,
    SearchResponse,
    SourceInfo,
    SourceWarning,
    Track,
    TrackAvailability,
    TrackSourceRef,
)
from app.interfaces.music_provider import IMusicProvider
from app.providers.soundcloud import SoundCloudProvider
from app.providers.spotify import SpotifyProvider
from app.providers.vk import VKProvider
from app.providers.youtube import YouTubeProvider
from app.providers.ytmusic import YTMusicProvider
from app.services.device_library import DeviceLibraryService
from app.services.errors import (
    BadRequestError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.services.media_cache import MediaCacheService
from app.services.search_result_store import SearchResultStore
from app.services.track_identity import (
    hydrate_device_track_keys,
    hydrate_track_keys,
    tracks_match,
)


@dataclass
class ProviderEntry:
    info: SourceInfo
    provider: Optional[IMusicProvider]
    priority: int


class MusicManager:
    def __init__(self) -> None:
        cache_root = Path(
            os.getenv(
                "SLEEWAVE_CACHE_DIR",
                str(Path(tempfile.gettempdir()) / "sleewave-media-cache"),
            )
        )
        max_cache_mb = int(os.getenv("SLEEWAVE_CACHE_MAX_MB", "1024"))
        self.cache = MediaCacheService(
            cache_root,
            max_size_bytes=max_cache_mb * 1024 * 1024,
        )
        self.device_library = DeviceLibraryService(cache_root / "device_library.json")
        self.search_results = SearchResultStore(
            cache_root / "search_results.json",
            ttl_seconds=int(os.getenv("SLEEWAVE_SEARCH_RESULT_TTL_SECONDS", "1800")),
        )
        self._providers: dict[str, ProviderEntry] = {
            "ytm": ProviderEntry(
                info=SourceInfo(id="ytm", name="YouTube Music"),
                provider=YTMusicProvider(),
                priority=10,
            ),
            "yt": ProviderEntry(
                info=SourceInfo(id="yt", name="YouTube"),
                provider=YouTubeProvider(),
                priority=20,
            ),
            "sc": ProviderEntry(
                info=SourceInfo(id="sc", name="SoundCloud"),
                provider=SoundCloudProvider(),
                priority=30,
            ),
            "spotify": ProviderEntry(
                info=SourceInfo(
                    id="spotify",
                    name="Spotify",
                    available=False,
                    message="Spotify integration has not been implemented yet.",
                ),
                provider=SpotifyProvider(),
                priority=40,
            ),
            "vk": ProviderEntry(
                info=SourceInfo(
                    id="vk",
                    name="VK Music",
                    available=False,
                    message="VK integration has not been implemented yet.",
                ),
                provider=VKProvider(),
                priority=50,
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
        cleaned_query = query.strip()
        if not cleaned_query:
            raise BadRequestError("The search query cannot be empty.")
        if limit < 1 or limit > 100:
            raise BadRequestError("The limit must be between 1 and 100.")
        if offset < 0:
            raise BadRequestError("The offset cannot be negative.")

        source_ids = self._resolve_sources(source_selector)
        fetch_limit = min(limit + offset + 10, 100)

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
        self.search_results.store_tracks(paged_results)
        return SearchResponse(
            query=cleaned_query,
            sources=source_ids,
            results=paged_results,
            warnings=warnings,
        )

    async def prepare_track(self, result_id: str) -> PreparedTrackResponse:
        track = hydrate_track_keys(self.search_results.get_track(result_id))

        async def downloader(target_path: str) -> Optional[str]:
            provider = self._get_download_provider(track.source)
            return await provider.download(track.id, target_path)

        record, cache_hit = await self.cache.ensure_cached(track, downloader)
        track.availability = TrackAvailability(
            in_server_cache=True,
            on_device=False,
            cache_key=record.cache_key,
            preferred_origin="server",
        )
        return PreparedTrackResponse(
            track=track,
            cache_key=record.cache_key,
            cache_hit=cache_hit,
            stream_url=f"/media/{record.cache_key}/stream",
            download_url=f"/media/{record.cache_key}/download",
        )

    def sync_device_library(self, request: DeviceLibrarySyncRequest) -> DeviceLibrarySyncResponse:
        track_count = self.device_library.replace_tracks(request)
        exact_keys, base_keys = self.device_library.iter_device_track_keys(request.device_id)
        removed_cache_entries = self.cache.delete_by_keys(exact_keys, base_keys)
        return DeviceLibrarySyncResponse(
            device_id=request.device_id,
            track_count=track_count,
            removed_cache_entries=removed_cache_entries,
        )

    def confirm_device_download(
        self,
        request: DeviceDownloadConfirmationRequest,
    ) -> DeviceDownloadConfirmationResponse:
        hydrated_request_track = hydrate_device_track_keys(
            DeviceLibraryTrack(
                title=request.title,
                artist=request.artist,
                duration=request.duration,
                album=request.album,
                track_key=request.track_key,
                base_track_key=request.base_track_key,
            )
        )
        self.device_library.add_track(request.device_id, hydrated_request_track)
        removed = self.cache.delete_by_keys(
            {key for key in [hydrated_request_track.track_key] if key},
            {key for key in [hydrated_request_track.base_track_key] if key},
        )
        return DeviceDownloadConfirmationResponse(
            device_id=request.device_id,
            removed_from_server_cache=removed > 0,
        )

    def get_cached_record(self, cache_key: str):
        self.cache.touch(cache_key)
        return self.cache.get_by_cache_key(cache_key)

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
        for track in tracks:
            cache_record = self.cache.find_by_track(track)
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
