import asyncio
from ytmusicapi import YTMusic
from yt_dlp import YoutubeDL
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track

class YTMusicProvider(IMusicProvider):
    def __init__(self):
        self.ytm = YTMusic()
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_event_loop()
        
        # Запрашиваем с запасом, чтобы компенсировать отсутствие offset в API
        # и отфильтровать возможный мусор
        fetch_limit = limit + offset + 5 
        
        def fetch_metadata():
            # filter="songs" гарантирует, что мы не получим видео-интервью или плейлисты
            return self.ytm.search(query, filter="songs", limit=fetch_limit)

        try:
            results = await loop.run_in_executor(None, fetch_metadata)
            if not results:
                return []

            valid_tracks = []
            # Пропускаем offset и начинаем собирать треки
            for res in results[offset:]:
                # Проверка на наличие критически важных данных
                if not res.get('videoId') or not res.get('title'):
                    continue
                
                # Собираем модель Track
                track = Track(
                    id=res['videoId'],
                    title=res['title'],
                    artist=res['artists'][0]['name'] if res.get('artists') else "Unknown Artist",
                    source="ytmusic",
                    # В YTMusic длительность часто приходит строкой "3:45", 
                    # если твоя модель требует int (секунды), лучше перепроверить
                    duration=res.get('duration_seconds') or 0,
                    cover_url=res['thumbnails'][-1]['url'] if res.get('thumbnails') else None,
                    stream_url=None
                )
                valid_tracks.append(track)
                
                if len(valid_tracks) >= limit:
                    break
                    
            return valid_tracks

        except Exception as e:
            print(f"❌ YTMusic Search Error: {e}")
            return []

    async def get_stream(self, track_id: str) -> str:
        """
        Получает прямую ссылку на аудио через yt-dlp.
        """
        loop = asyncio.get_event_loop()
        url = f"https://www.youtube.com/watch?v={track_id}"
        
        def extract_url():
            with YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get('url')

        try:
            stream_url = await loop.run_in_executor(None, extract_url)
            return stream_url if stream_url else ""
        except Exception as e:
            print(f"❌ YTMusic Stream Error: {e}")
            return ""