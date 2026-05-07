import asyncio
from vk_api import VkApi
from vk_api.audio import VkAudio
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track
import os
from dotenv import load_dotenv

load_dotenv()

class VKProvider(IMusicProvider):
    def __init__(self):
        # Авторизация. Лучше использовать логин/пароль технического аккаунта
        self.login = os.getenv("VK_LOGIN")
        self.password = os.getenv("VK_PASSWORD")
        
        self.vk_session = VkApi(self.login, self.password)
        try:
            self.vk_session.auth()
        except Exception as e:
            print(f"error VK auth: {e}")
            
        self.vkaudio = VkAudio(self.vk_session)

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_event_loop()
        
        # ВК API синхронный, поэтому снова используем executor
        def fetch_vk():
            # search возвращает генератор, превращаем его в список с учетом лимитов
            results = self.vkaudio.search(q=query, count=limit, offset=offset)
            return list(results)

        data = await loop.run_in_executor(None, fetch_vk)
        
        tracks = []
        for entry in data:
            tracks.append(Track(
                id=f"{entry['owner_id']}_{entry['id']}", # Уникальный ID в ВК
                title=entry.get('title'),
                artist=entry.get('artist'),
                source='vk',
                duration=entry.get('duration', 0),
                cover_url=entry.get('track_covers', [None])[0], # Берем первую обложку
                stream_url=entry.get('url') # Ссылка на m3u8
            ))
        return tracks

    async def get_stream(self, track_id: str) -> str:
        # В ВК ссылка на поток обычно идет сразу в поиске, 
        # но она быстро протухает. По-хорошему, здесь нужно 
        # переполучать трек по ID.
        owner_id, audio_id = track_id.split('_')
        # Логика переполучения свежей ссылки...
        return "URL_TO_M3U8"