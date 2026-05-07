from __future__ import annotations

from typing import Optional

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider


class VKProvider(IMusicProvider):
    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        return []

    async def get_stream(self, track_id: str) -> str:
        return ""

    async def download(self, track_id: str, output_path: str) -> Optional[str]:
        return None
