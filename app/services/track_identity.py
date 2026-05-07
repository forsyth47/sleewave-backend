from __future__ import annotations

import hashlib
import re
import unicodedata

from app.domain.models import DeviceLibraryTrack, Track

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    compact = _NON_ALNUM_RE.sub(" ", ascii_only.lower()).strip()
    return re.sub(r"\s+", " ", compact)


def build_base_track_key(title: str, artist: str) -> str:
    payload = f"{normalize_text(artist)}|{normalize_text(title)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def build_track_key(title: str, artist: str, duration: int = 0) -> str:
    payload = build_base_track_key(title, artist)
    if duration and duration > 0:
        duration_bucket = round(duration / 5)
        payload = f"{payload}|{duration_bucket}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def hydrate_track_keys(track: Track) -> Track:
    track.base_track_key = track.base_track_key or build_base_track_key(track.title, track.artist)
    track.track_key = track.track_key or build_track_key(track.title, track.artist, track.duration)
    return track


def hydrate_device_track_keys(track: DeviceLibraryTrack) -> DeviceLibraryTrack:
    track.base_track_key = track.base_track_key or build_base_track_key(track.title, track.artist)
    track.track_key = track.track_key or build_track_key(track.title, track.artist, track.duration)
    return track


def tracks_match(left: Track, right: Track) -> bool:
    if hydrate_track_keys(left).base_track_key != hydrate_track_keys(right).base_track_key:
        return False
    if left.duration > 0 and right.duration > 0:
        return abs(left.duration - right.duration) <= 6
    return True
