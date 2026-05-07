from app.interfaces.music_provider import IMusicProvider
from app.providers.youtube import YouTubeProvider
from app.providers.vk import VKProvider
from app.providers.soundcloud import SoundCloudProvider

class MusicManager:
    def __init__(self):
        self._providers: dict[str, IMusicProvider] = {
            "youtube": YouTubeProvider(),
            "vk": VKProvider(),
            "soundcloud": SoundCloudProvider()
        }

    async def search(self, source: str, query: str, limit: int = 10, offset: int = 0):
        provider = self._providers.get(source)
        if not provider:
            return {"error": "Source not found"}
        return await provider.search(query, limit, offset)

    async def get_stream(self, source: str, track_id: str):
        provider = self._providers.get(source)
        return await provider.get_stream(track_id)