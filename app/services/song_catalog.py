from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.domain.models import SongCatalogRecord, Track, TrackAvailability, TrackSourceRef
from app.services.errors import SearchResultNotFoundError
from app.services.track_identity import hydrate_track_keys


def _model_to_dict(model):
    if hasattr(model, "model_dump_json"):
        return json.loads(model.model_dump_json())
    if hasattr(model, "json"):
        return json.loads(model.json())
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


class SongCatalog:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def upsert_tracks(self, tracks: list[Track]) -> list[Track]:
        payload = self._load()
        now = datetime.now(timezone.utc)
        hydrated_tracks = [self._upsert_track(payload, track, now) for track in tracks]
        self._save(payload)
        return hydrated_tracks

    def upsert_track(self, track: Track) -> Track:
        return self.upsert_tracks([track])[0]

    def get_track(self, result_id: str) -> Track:
        payload = self._load()
        for item in payload["records"]:
            if item.get("result_id") == result_id:
                return self._track_from_record(SongCatalogRecord(**item))
        raise SearchResultNotFoundError(result_id)

    def delete_track(self, result_id: str) -> bool:
        payload = self._load()
        original_count = len(payload["records"])
        payload["records"] = [
            item for item in payload["records"] if item.get("result_id") != result_id
        ]
        deleted = len(payload["records"]) != original_count
        if deleted:
            self._save(payload)
        return deleted

    def clear(self) -> None:
        self._save({"records": []})

    def _upsert_track(self, payload: dict, track: Track, now: datetime) -> Track:
        hydrated_track = hydrate_track_keys(track)
        result_id = hydrated_track.track_key or hydrated_track.base_track_key or ""
        hydrated_track.result_id = result_id

        existing = next(
            (item for item in payload["records"] if item.get("result_id") == result_id),
            None,
        )
        if existing:
            existing_record = SongCatalogRecord(**existing)
            hydrated_track.alternate_sources = self._merge_sources(
                self._track_from_record(existing_record),
                hydrated_track,
            )
            created_at = existing.get("created_at", now.isoformat())
        else:
            created_at = now

        record = SongCatalogRecord(
            result_id=result_id,
            source=hydrated_track.source,
            track_id=hydrated_track.id,
            title=hydrated_track.title,
            artist=hydrated_track.artist,
            duration=hydrated_track.duration,
            cover_url=hydrated_track.cover_url,
            album=hydrated_track.album,
            track_key=hydrated_track.track_key or result_id,
            base_track_key=hydrated_track.base_track_key or result_id,
            alternate_sources=hydrated_track.alternate_sources,
            created_at=created_at,
            updated_at=now,
        )

        record_payload = _model_to_dict(record)
        if existing:
            existing.update(record_payload)
        else:
            payload["records"].append(record_payload)
        return hydrated_track

    def _merge_sources(self, existing: Track, incoming: Track) -> list[TrackSourceRef]:
        refs = {(existing.source, existing.id)}
        merged = list(existing.alternate_sources)
        refs.update((ref.source, ref.track_id) for ref in merged)

        incoming_ref = (incoming.source, incoming.id)
        if incoming_ref != (existing.source, existing.id):
            merged.append(TrackSourceRef(source=existing.source, track_id=existing.id))
            refs.add((existing.source, existing.id))
        for ref in incoming.alternate_sources:
            key = (ref.source, ref.track_id)
            if key not in refs:
                refs.add(key)
                merged.append(ref)
        if incoming_ref not in refs and incoming_ref != (existing.source, existing.id):
            merged.append(TrackSourceRef(source=incoming.source, track_id=incoming.id))

        deduped = []
        seen_refs = {(incoming.source, incoming.id)}
        for ref in merged:
            key = (ref.source, ref.track_id)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            deduped.append(ref)
        return deduped

    def _track_from_record(self, record: SongCatalogRecord) -> Track:
        return Track(
            id=record.track_id,
            title=record.title,
            artist=record.artist,
            source=record.source,
            duration=record.duration,
            cover_url=record.cover_url,
            album=record.album,
            result_id=record.result_id,
            track_key=record.track_key,
            base_track_key=record.base_track_key,
            alternate_sources=record.alternate_sources,
            availability=TrackAvailability(),
        )

    def _load(self) -> dict:
        if not self.storage_path.exists():
            return {"records": []}
        with self.storage_path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if "records" not in payload or not isinstance(payload["records"], list):
            payload["records"] = []
        return self._dedupe(payload)

    def _dedupe(self, payload: dict) -> dict:
        deduped_records = {}
        for item in payload["records"]:
            result_id = item.get("result_id")
            if result_id:
                deduped_records[result_id] = item
        if len(deduped_records) != len(payload["records"]):
            payload["records"] = list(deduped_records.values())
            self._save(payload)
        return payload

    def _save(self, payload: dict) -> None:
        with self.storage_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=True, separators=(",", ":"))
