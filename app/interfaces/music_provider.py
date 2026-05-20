from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.domain.models import Track


class IMusicProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        """Search tracks for a query."""

    @abstractmethod
    async def get_stream(self, track_id: str) -> str:
        """Return a direct upstream stream URL when available."""

    @abstractmethod
    async def download(
        self,
        track_id: str,
        output_path: str,
        stream_url: Optional[str] = None,
    ) -> Optional[str]:
        """Download the track and return the final file path."""
