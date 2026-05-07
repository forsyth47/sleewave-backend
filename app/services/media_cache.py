from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from mutagen.id3 import ID3, TIT2, TPE1
from mutagen.mp3 import MP3

from app.domain.models import CacheRecord, Track
from app.services.errors import CacheEntryNotFoundError, TrackPreparationError
from app.services.track_identity import hydrate_track_keys

logger = logging.getLogger(__name__)


def _model_to_dict(model):
    if hasattr(model, "model_dump_json"):
        return json.loads(model.model_dump_json())
    if hasattr(model, "json"):
        return json.loads(model.json())
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


class MediaCacheService:
    def __init__(
        self,
        cache_root: Path,
        *,
        max_size_bytes: int,
    ) -> None:
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_root / "cache_index.json"
        self.files_dir = self.cache_root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_bytes
        self._clean_missing_files()

    async def ensure_cached(
        self,
        track: Track,
        downloader: Callable[[str], Awaitable[Optional[str]]],
    ) -> tuple[CacheRecord, bool]:
        hydrated_track = hydrate_track_keys(track)
        existing = self.find_by_track(hydrated_track)
        if existing:
            self.touch(existing.cache_key)
            return existing, True

        cache_key = hydrated_track.track_key or ""
        final_path = self.files_dir / f"{cache_key}.mp3"

        try:
            downloaded_path = await downloader(str(final_path))
        except Exception as exc:
            raise TrackPreparationError(
                hydrated_track.source,
                hydrated_track.id,
                "The track could not be downloaded from the selected source.",
                cause=str(exc),
            ) from exc

        if not downloaded_path:
            raise TrackPreparationError(
                hydrated_track.source,
                hydrated_track.id,
                "The track could not be downloaded from the selected source.",
            )

        actual_path = Path(downloaded_path)
        if actual_path != final_path:
            if final_path.exists():
                final_path.unlink()
            actual_path.replace(final_path)

        self._write_basic_metadata(final_path, hydrated_track)
        now = datetime.now(timezone.utc)
        record = CacheRecord(
            cache_key=cache_key,
            file_path=str(final_path),
            file_size=final_path.stat().st_size,
            source=hydrated_track.source,
            track_id=hydrated_track.id,
            title=hydrated_track.title,
            artist=hydrated_track.artist,
            duration=hydrated_track.duration,
            cover_url=hydrated_track.cover_url,
            album=hydrated_track.album,
            track_key=hydrated_track.track_key or "",
            base_track_key=hydrated_track.base_track_key or "",
            created_at=now,
            last_accessed_at=now,
        )
        index = self._load_index()
        index["records"] = [item for item in index["records"] if item.get("cache_key") != cache_key]
        index["records"].append(_model_to_dict(record))
        self._save_index(index)
        self._evict_if_needed()
        return record, False

    def find_by_track(self, track: Track) -> Optional[CacheRecord]:
        hydrated_track = hydrate_track_keys(track)
        index = self._load_index()
        exact_match = None
        base_match = None
        for item in index["records"]:
            record = CacheRecord(**item)
            if not Path(record.file_path).exists():
                continue
            if item.get("track_key") == hydrated_track.track_key:
                exact_match = record
                break
            if item.get("base_track_key") == hydrated_track.base_track_key and base_match is None:
                base_match = record
        return exact_match or base_match

    def get_by_cache_key(self, cache_key: str) -> CacheRecord:
        index = self._load_index()
        for item in index["records"]:
            if item.get("cache_key") == cache_key:
                record = CacheRecord(**item)
                if Path(record.file_path).exists():
                    return record
                self.delete(cache_key)
                break
        raise CacheEntryNotFoundError(cache_key)

    def delete(self, cache_key: str) -> bool:
        index = self._load_index()
        removed = False
        updated_records = []
        for item in index["records"]:
            if item.get("cache_key") == cache_key:
                removed = True
                file_path = Path(item["file_path"])
                if file_path.exists():
                    file_path.unlink()
                continue
            updated_records.append(item)
        if removed:
            index["records"] = updated_records
            self._save_index(index)
        return removed

    def delete_by_track(self, track: Track) -> bool:
        record = self.find_by_track(track)
        if not record:
            return False
        return self.delete(record.cache_key)

    def delete_by_keys(self, track_keys: set[str], base_track_keys: set[str]) -> int:
        index = self._load_index()
        removed_count = 0
        updated_records = []
        for item in index["records"]:
            exact_key = item.get("track_key")
            base_key = item.get("base_track_key")
            should_remove = exact_key in track_keys or base_key in base_track_keys
            if should_remove:
                file_path = Path(item["file_path"])
                if file_path.exists():
                    file_path.unlink()
                removed_count += 1
                continue
            updated_records.append(item)
        if removed_count:
            index["records"] = updated_records
            self._save_index(index)
        return removed_count

    def touch(self, cache_key: str) -> None:
        index = self._load_index()
        now = datetime.now(timezone.utc).isoformat()
        changed = False
        for item in index["records"]:
            if item.get("cache_key") == cache_key:
                item["last_accessed_at"] = now
                changed = True
                break
        if changed:
            self._save_index(index)

    def _evict_if_needed(self) -> None:
        index = self._load_index()
        total_size = sum(item.get("file_size", 0) for item in index["records"])
        if total_size <= self.max_size_bytes:
            return

        sorted_records = sorted(index["records"], key=lambda item: item.get("last_accessed_at", ""))
        for item in sorted_records:
            if total_size <= self.max_size_bytes:
                break
            file_path = Path(item["file_path"])
            if file_path.exists():
                file_path.unlink()
            total_size -= item.get("file_size", 0)
            index["records"] = [record for record in index["records"] if record.get("cache_key") != item.get("cache_key")]

        self._save_index(index)

    def _clean_missing_files(self) -> None:
        index = self._load_index()
        cleaned_records = []
        for item in index["records"]:
            if Path(item["file_path"]).exists():
                cleaned_records.append(item)
        if len(cleaned_records) != len(index["records"]):
            index["records"] = cleaned_records
            self._save_index(index)

    def _write_basic_metadata(self, file_path: Path, track: Track) -> None:
        try:
            audio = MP3(file_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("TIT2")
            audio.tags.delall("TPE1")
            audio.tags.add(TIT2(encoding=3, text=track.title))
            audio.tags.add(TPE1(encoding=3, text=track.artist))
            audio.save()
        except Exception as exc:
            logger.warning("Could not write metadata for %s: %s", file_path, exc)

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {"records": []}
        with self.index_path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def _save_index(self, payload: dict) -> None:
        with self.index_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=True, indent=2)
