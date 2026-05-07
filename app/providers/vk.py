import asyncio
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track

class VKProvider(IMusicProvider):
    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        # TODO: Implement VK search
        return []

    async def get_stream(self, track_id: str) -> str:
        # TODO: Implement VK stream
        return ""

    async def download(self, track_id: str, output_path: str) -> bool:
        # TODO: Implement VK download
        return False