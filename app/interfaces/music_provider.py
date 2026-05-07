from abc import ABC, abstractmethod
from app.domain.models import Track

class IMusicProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        """search for tracks based on a query string"""
        pass

    @abstractmethod
    async def get_stream(self, track_id: str) -> str:
        """get the direct stream URL for a track"""
        pass