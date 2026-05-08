from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider
from app.providers.download_helpers import ensure_ffmpeg_available


def _best_thumbnail(entry: dict) -> Optional[str]:
    thumbnails = entry.get("thumbnails") or []
    if thumbnails:
        best = max(
            thumbnails,
            key=lambda item: (item.get("width") or 0) * (item.get("height") or 0),
        )
        return best.get("url")
    return entry.get("thumbnail")


class YouTubeProvider(IMusicProvider):
    def __init__(self) -> None:
        self.common_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "ytsearch",
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_running_loop()
        search_query = f"ytsearch{limit + offset}:{query}"

        def fetch_results():
            with YoutubeDL(self.common_options) as ydl:
                return ydl.extract_info(search_query, download=False)

        payload = await loop.run_in_executor(None, fetch_results)
        entries = payload.get("entries", []) if payload else []
        results = []
        for entry in entries[offset : offset + limit]:
            if not entry or not entry.get("id"):
                continue
            results.append(
                Track(
                    id=entry["id"],
                    title=entry.get("title", "Unknown Title"),
                    artist=entry.get("uploader", "Unknown Artist"),
                    source="yt",
                    duration=int(entry.get("duration") or 0),
                    cover_url=_best_thumbnail(entry),
                )
            )
        return results

    async def get_stream(self, track_id: str) -> str:
        loop = asyncio.get_running_loop()
        url = f"https://www.youtube.com/watch?v={track_id}"

        def extract_stream_url():
            with YoutubeDL(self.common_options) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("url", "")

        return await loop.run_in_executor(None, extract_stream_url)

    async def download(self, track_id: str, output_path: str) -> Optional[str]:
        loop = asyncio.get_running_loop()
        url = f"https://www.youtube.com/watch?v={track_id}"
        final_path = Path(output_path)
        output_template = str(final_path.with_suffix(".%(ext)s"))

        download_options = {
            **self.common_options,
            "format": "bestaudio/best",
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
                ydl.download([url])
            return str(final_path)

        try:
            return await loop.run_in_executor(None, download_track)
        except Exception as exc:
            raise RuntimeError(f"YouTube download failed: {exc}") from exc
