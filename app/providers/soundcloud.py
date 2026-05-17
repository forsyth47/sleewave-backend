from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider
from app.providers.download_helpers import ensure_ffmpeg_available, ytdlp_auth_options

logger = logging.getLogger(__name__)


def _high_res_artwork(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return (
        url.replace("-large.jpg", "-t500x500.jpg")
        .replace("large.jpg", "t500x500.jpg")
        .replace("-t300x300.jpg", "-t500x500.jpg")
        .replace("t300x300.jpg", "t500x500.jpg")
        .replace("-crop.jpg", "-t500x500.jpg")
        .replace("crop.jpg", "t500x500.jpg")
    )


class _YtDlpLogger:
    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        logger.debug("SoundCloud yt-dlp warning: %s", message)

    def error(self, message: str) -> None:
        logger.debug("SoundCloud yt-dlp error: %s", message)


def _soundcloud_track_url(entry: dict) -> Optional[str]:
    for key in ("webpage_url", "permalink_url", "original_url", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith("https://soundcloud.com/"):
            return value
    return None


def _duration_seconds(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _best_artwork(entry: dict) -> Optional[str]:
    thumbnails = entry.get("thumbnails") or []
    if thumbnails:
        best = max(
            thumbnails,
            key=lambda item: (item.get("width") or 0) * (item.get("height") or 0),
        )
        if best.get("url"):
            return _high_res_artwork(best["url"])

    for key in ("thumbnail", "artwork_url", "thumbnail_url", "avatar_url"):
        value = entry.get(key)
        if value:
            return _high_res_artwork(value)
    return None


class SoundCloudProvider(IMusicProvider):
    def __init__(self) -> None:
        self.common_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "suppress_warnings": True,
            "logger": _YtDlpLogger(),
            "socket_timeout": 15,
            "retries": 2,
            "fragment_retries": 2,
            **ytdlp_auth_options(),
        }
        self.search_options = {
            **self.common_options,
            "extract_flat": "in_playlist",
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_running_loop()
        search_query = f"scsearch{limit + offset + 10}:{query}"

        def fetch_results():
            with YoutubeDL(self.search_options) as ydl:
                return ydl.extract_info(search_query, download=False)

        payload = await loop.run_in_executor(None, fetch_results)
        entries = payload.get("entries", []) if payload else []
        results = []
        for entry in entries[offset:]:
            if not entry:
                continue
            track_url = _soundcloud_track_url(entry)
            if not track_url:
                continue
            results.append(
                Track(
                    id=track_url,
                    title=entry.get("title", "Unknown Title"),
                    artist=entry.get("uploader") or entry.get("channel") or "Unknown Artist",
                    source="sc",
                    duration=_duration_seconds(entry.get("duration")),
                    cover_url=_best_artwork(entry),
                    album=entry.get("album"),
                )
            )
            if len(results) >= limit:
                break
        return results

    async def get_stream(self, track_id: str) -> str:
        loop = asyncio.get_running_loop()

        def extract_stream_url():
            with YoutubeDL(self.common_options) as ydl:
                info = ydl.extract_info(track_id, download=False)
                return info.get("url", "") if info else ""

        return await loop.run_in_executor(None, extract_stream_url)

    async def download(self, track_id: str, output_path: str) -> Optional[str]:
        loop = asyncio.get_running_loop()
        final_path = Path(output_path)
        output_template = str(final_path.with_suffix(".%(ext)s"))

        download_options = {
            **self.common_options,
            "ignoreerrors": False,
            "outtmpl": output_template,
            "overwrites": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "final_ext": "mp3",
        }

        def download_track():
            ensure_ffmpeg_available()
            with YoutubeDL(download_options) as ydl:
                ydl.download([track_id])
            if not final_path.exists():
                raise RuntimeError("SoundCloud download finished without creating an MP3 file.")
            return str(final_path)

        try:
            return await loop.run_in_executor(None, download_track)
        except Exception as exc:
            raise RuntimeError(f"SoundCloud download failed: {exc}") from exc
