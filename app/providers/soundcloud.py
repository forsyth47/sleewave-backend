from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider
from app.providers.download_helpers import ensure_ffmpeg_available


def _high_res_artwork(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return (
        url.replace("large.jpg", "t500x500.jpg")
        .replace("t300x300.jpg", "t500x500.jpg")
        .replace("crop.jpg", "t500x500.jpg")
    )


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
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_running_loop()
        search_query = f"scsearch{limit + offset + 10}:{query}"

        def fetch_results():
            with YoutubeDL(self.common_options) as ydl:
                return ydl.extract_info(search_query, download=False)

        payload = await loop.run_in_executor(None, fetch_results)
        entries = payload.get("entries", []) if payload else []
        results = []
        for entry in entries[offset:]:
            if not entry or not entry.get("url"):
                continue
            results.append(
                Track(
                    id=entry.get("webpage_url") or entry.get("url"),
                    title=entry.get("title", "Unknown Title"),
                    artist=entry.get("uploader", "Unknown Artist"),
                    source="sc",
                    duration=int(entry.get("duration") or 0),
                    cover_url=_high_res_artwork(entry.get("thumbnail")),
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
            return str(final_path)

        try:
            return await loop.run_in_executor(None, download_track)
        except Exception as exc:
            raise RuntimeError(f"SoundCloud download failed: {exc}") from exc
