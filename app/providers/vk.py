import asyncio
from vk_api import VkApi
from vk_api.audio import VkAudio
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track
import os

class VKProvider(IMusicProvider):
    def __init__(self):
        self.token = os.getenv("VK_TOKEN")
        self.vk_session = None
        self.vkaudio = None

    def _auth(self):
        """Авторизация через токен (без логина/пароля)"""
        if self.vk_session is None:
            # Передаем токен напрямую
            self.vk_session = VkApi(token=self.token)
            # VkAudio все равно требует инициализации сессии
            self.vkaudio = VkAudio(self.vk_session)

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_event_loop()
        
        def fetch_vk():
            self._auth()
            # Используем метод поиска через аудио-модуль
            return list(self.vkaudio.search(q=query, count=limit, offset=offset))

        try:
            data = await loop.run_in_executor(None, fetch_vk)
            
            tracks = []
            for entry in data:
                # В ВК структура ответа может меняться, проверяем обложки
                cover = None
                if 'track_covers' in entry:
                    cover = entry['track_covers'][0]
                elif 'album' in entry and 'thumb' in entry['album']:
                    cover = entry['album']['thumb'].get('photo_600')

                tracks.append(Track(
                    id=f"{entry['owner_id']}_{entry['id']}",
                    title=entry.get('title', 'Unknown'),
                    artist=entry.get('artist', 'Unknown'),
                    source='vk',
                    duration=entry.get('duration', 0),
                    cover_url=cover,
                    stream_url=entry.get('url')  # Ссылка на m3u8
                ))
            return tracks
        except Exception as e:
            print(f"VK Search Error: {e}")
            return []

    async def get_stream(self, track_id: str) -> str:
        # Обычно ссылка уже есть в объекте трека после поиска, 
        # но если она протухла, здесь можно реализовать переполучение.
        return ""