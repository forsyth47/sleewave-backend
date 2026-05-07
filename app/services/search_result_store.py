from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain.models import SearchResultRecord, Track
from app.services.errors import SearchResultNotFoundError


def _model_to_dict(model):
    if hasattr(model, "model_dump_json"):
        return json.loads(model.model_dump_json())
    if hasattr(model, "json"):
        return json.loads(model.json())
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


class SearchResultStore:
    def __init__(self, storage_path: Path, *, ttl_seconds: int) -> None:
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def store_tracks(self, tracks: list[Track]) -> list[Track]:
        payload = self._load()
        now = datetime.now(timezone.utc)
        self._cleanup_payload(payload, now)

        hydrated_tracks: list[Track] = []
        for track in tracks:
            result_id = secrets.token_urlsafe(9)
            track.result_id = result_id
            record = SearchResultRecord(
                result_id=result_id,
                track=track,
                created_at=now,
                expires_at=now + timedelta(seconds=self.ttl_seconds),
            )
            payload["records"].append(_model_to_dict(record))
            hydrated_tracks.append(track)

        self._save(payload)
        return hydrated_tracks

    def get_track(self, result_id: str) -> Track:
        payload = self._load()
        now = datetime.now(timezone.utc)
        changed = self._cleanup_payload(payload, now)

        for item in payload["records"]:
            if item.get("result_id") != result_id:
                continue
            record = SearchResultRecord(**item)
            if record.expires_at <= now:
                changed = True
                payload["records"] = [
                    existing for existing in payload["records"] if existing.get("result_id") != result_id
                ]
                break
            if changed:
                self._save(payload)
            return record.track

        if changed:
            self._save(payload)
        raise SearchResultNotFoundError(result_id)

    def _cleanup_payload(self, payload: dict, now: datetime) -> bool:
        original_count = len(payload["records"])
        payload["records"] = [
            item
            for item in payload["records"]
            if SearchResultRecord(**item).expires_at > now
        ]
        return len(payload["records"]) != original_count

    def _load(self) -> dict:
        if not self.storage_path.exists():
            return {"records": []}
        with self.storage_path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def _save(self, payload: dict) -> None:
        with self.storage_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=True, indent=2)
