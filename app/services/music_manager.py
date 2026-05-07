from app.interfaces.music_provider import IMusicProvider
from app.providers.youtube import YouTubeProvider
from app.providers.vk import VKProvider
from app.providers.soundcloud import SoundCloudProvider
from app.providers.ytmusic import YTMusicProvider

class MusicManager:
    def __init__(self):
        self._providers: dict[str, IMusicProvider] = {
            "yt": YouTubeProvider(),
            "vk": VKProvider(),
            "sc": SoundCloudProvider(),
            "ytm": YTMusicProvider()  # Временно используем YouTubeProvider для YTM, можно заменить на отдельный класс
        }

    async def search(self, source: str, query: str, limit: int = 10, offset: int = 0):
        provider = self._providers.get(source)
        if not provider:
            return {"error": "Source not found"}
        return await provider.search(query, limit, offset)

    async def get_stream(self, source: str, track_id: str):
        provider = self._providers.get(source)
        return await provider.get_stream(track_id)