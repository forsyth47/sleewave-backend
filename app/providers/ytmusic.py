from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider
from app.providers.download_helpers import ensure_ffmpeg_available


class YTMusicProvider(IMusicProvider):
    def __init__(self) -> None:
        self.ytm = YTMusic()
        self.common_options = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_running_loop()
        fetch_limit = limit + offset + 5

        def fetch_results():
            return self.ytm.search(query, filter="songs", limit=fetch_limit)

        payload = await loop.run_in_executor(None, fetch_results)
        results = []
        for item in payload[offset:]:
            if not item.get("videoId") or not item.get("title"):
                continue
            results.append(
                Track(
                    id=item["videoId"],
                    title=item["title"],
                    artist=item["artists"][0]["name"] if item.get("artists") else "Unknown Artist",
                    source="ytm",
                    duration=int(item.get("duration_seconds") or 0),
                    cover_url=item["thumbnails"][-1]["url"] if item.get("thumbnails") else None,
                    album=item.get("album", {}).get("name") if item.get("album") else None,
                )
            )
            if len(results) >= limit:
                break
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
        url = f"https://music.youtube.com/watch?v={track_id}"
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
                ydl.download([url])
            return str(final_path)

        try:
            return await loop.run_in_executor(None, download_track)
        except Exception as exc:
            raise RuntimeError(f"YTMusic download failed: {exc}") from exc
