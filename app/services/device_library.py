from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import DeviceLibrarySyncRequest, DeviceTrackRef, Track
from app.services.track_identity import hydrate_device_track_keys, hydrate_track_keys


def _model_to_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class DeviceLibraryService:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def replace_tracks(self, request: DeviceLibrarySyncRequest) -> int:
        payload = self._load()
        payload["devices"][request.device_id] = {
            "tracks": [_model_to_dict(hydrate_device_track_keys(track)) for track in request.tracks],
        }
        self._save(payload)
        return len(request.tracks)

    def has_track(self, device_id: str, track: Track) -> bool:
        device_tracks = self._load()["devices"].get(device_id, {}).get("tracks", [])
        hydrated_track = hydrate_track_keys(track)
        for item in device_tracks:
            if hydrated_track.track_key and item.get("track_key") == hydrated_track.track_key:
                return True
            if hydrated_track.base_track_key and item.get("base_track_key") == hydrated_track.base_track_key:
                return True
        return False

    def add_track(self, device_id: str, track: DeviceTrackRef) -> bool:
        payload = self._load()
        hydrated_track = hydrate_device_track_keys(track)
        device_payload = payload["devices"].setdefault(device_id, {"tracks": []})
        current_tracks = device_payload["tracks"]

        for item in current_tracks:
            if hydrated_track.track_key and item.get("track_key") == hydrated_track.track_key:
                self._save(payload)
                return False
            if hydrated_track.base_track_key and item.get("base_track_key") == hydrated_track.base_track_key:
                self._save(payload)
                return False

        current_tracks.append(_model_to_dict(hydrate_device_track_keys(track)))
        self._save(payload)
        return True

    def iter_device_track_keys(self, device_id: str) -> tuple[set[str], set[str]]:
        device_tracks = self._load()["devices"].get(device_id, {}).get("tracks", [])
        exact_keys = {item["track_key"] for item in device_tracks if item.get("track_key")}
        base_keys = {item["base_track_key"] for item in device_tracks if item.get("base_track_key")}
        return exact_keys, base_keys

    def _load(self) -> dict:
        if not self.storage_path.exists():
            return {"devices": {}}
        with self.storage_path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def _save(self, payload: dict) -> None:
        with self.storage_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=True, indent=2)
