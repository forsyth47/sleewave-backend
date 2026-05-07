import asyncio
from yt_dlp import YoutubeDL
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track

class SoundCloudProvider(IMusicProvider):
    def __init__(self):
        # Оптимизированные настройки для быстрого поиска в SoundCloud
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'scsearch', # Специальный префикс для SoundCloud
            'extract_flat': False,        # Нам нужны метаданные (длительность, обложка)
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_event_loop()
        
        # Запрос вида scsearch20:artist_name
        # Берем limit + offset, чтобы потом сделать срез
        search_query = f"scsearch{limit + offset}:{query}"
        
        def fetch_info():
            with YoutubeDL(self.ydl_opts) as ydl:
                return ydl.extract_info(search_query, download=False)

        try:
            data = await loop.run_in_executor(None, fetch_info)
            
            tracks = []
            if 'entries' in data:
                # Применяем offset и limit к результатам
                results = data['entries'][offset : offset + limit]
                
                for entry in results:
                    tracks.append(Track(
                        id=entry.get('id') or entry.get('url'),
                        title=entry.get('title', 'Unknown Title'),
                        artist=entry.get('uploader', 'Unknown Artist'),
                        source='soundcloud',
                        duration=int(entry.get('duration', 0)),
                        cover_url=entry.get('thumbnail'),
                        stream_url=None  # Ссылку получаем только при воспроизведении
                    ))
            return tracks
        except Exception as e:
            print(f"SoundCloud Search Error: {e}")
            return []

    async def get_stream(self, track_id: str) -> str:
        """
        Получает прямую ссылку на аудиопоток.
        track_id для SoundCloud через yt-dlp обычно является полным URL или ID трека.
        """
        loop = asyncio.get_event_loop()
        
        # Если пришел просто ID, достраиваем URL. Если уже URL — оставляем.
        url = track_id if track_id.startswith('http') else f"https://soundcloud.com/{track_id}"
        
        def extract_url():
            with YoutubeDL({'format': 'bestaudio/best', 'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get('url')

        try:
            return await loop.run_in_executor(None, extract_url)
        except Exception as e:
            print(f"SoundCloud Stream Error: {e}")
            return ""