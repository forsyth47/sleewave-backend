import asyncio
from yt_dlp import YoutubeDL
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track

class YouTubeProvider(IMusicProvider):
    def __init__(self):
        # Настройки для поиска и получения инфо
        self.ydl_opts = {
            'format': 'bestaudio/best', # Ищем только лучший звук
            'noplaylist': True,         # Нам нужны отдельные треки, а не плейлисты
            'quiet': True,              # Не забивать консоль лишним логом
            'no_warnings': True,
            'default_search': 'ytsearch', # Говорим, что будем искать в YouTube
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        # Запускаем тяжелый процесс поиска в отдельном потоке, чтобы не блокировать FastAPI
        loop = asyncio.get_event_loop()
        
        # ytsearch20:miyagi — это формат запроса для yt-dlp (искать 20 штук)
        search_query = f"ytsearch{limit + offset}:{query}"
        
        def download_info():
            with YoutubeDL(self.ydl_opts) as ydl:
                return ydl.extract_info(search_query, download=False)

        # Выполняем поиск
        data = await loop.run_in_executor(None, download_info)
        
        tracks = []
        if 'entries' in data:
            # Делаем срез (offset), чтобы пропустить первые результаты, если нужно
            results = data['entries'][offset : offset + limit]
            
            for entry in results:
                tracks.append(Track(
                    id=entry.get('id'),
                    title=entry.get('title'),
                    artist=entry.get('uploader', 'Unknown Artist'),
                    source='youtube',
                    duration=entry.get('duration', 0),
                    cover_url=entry.get('thumbnail'),
                    stream_url=None # Ссылку на поток будем получать только при клике Play
                ))
        return tracks

    async def get_stream(self, track_id: str) -> str:
        # Получаем прямую ссылку на .webm или .m4a файл
        loop = asyncio.get_event_loop()
        url = f"https://www.youtube.com/watch?v={track_id}"
        
        def extract_url():
            with YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get('url') # Это и есть прямая ссылка для плеера

        return await loop.run_in_executor(None, extract_url)