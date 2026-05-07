import asyncio
from yt_dlp import YoutubeDL
from app.interfaces.music_provider import IMusicProvider
from app.domain.models import Track

class SoundCloudProvider(IMusicProvider):
    def __init__(self):
        self.common_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,  # Пропускать ошибки (в т.ч. 404)
            'suppress_warnings': True,
        }

    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        loop = asyncio.get_event_loop()
        valid_tracks = []
        current_offset = offset
        attempts = 0
        max_attempts = 3  # Чтобы не долбиться вечно, если выдача совсем мертвая

        while len(valid_tracks) < limit and attempts < max_attempts:
            # Запрашиваем порцию (чуть больше лимита для эффективности)
            fetch_count = limit * 2 
            search_query = f"scsearch{fetch_count}:{query}"
            
            # В yt-dlp для поиска через scsearch нет параметра начала отсчета (offset),
            # поэтому мы просто берем большой кусок и вырезаем нужное.
            # Но чтобы "добирать", нам нужно увеличивать окно поиска.
            search_query = f"scsearch{current_offset + fetch_count}:{query}"

            def fetch_info():
                with YoutubeDL(self.common_opts) as ydl:
                    return ydl.extract_info(search_query, download=False)

            try:
                data = await loop.run_in_executor(None, fetch_info)
                if not data or 'entries' not in data:
                    break

                # Фильтруем то, что уже нашли, и убираем битые (None)
                new_entries = [
                    e for e in data['entries'][current_offset:] 
                    if e is not None and e.get('url')
                ]

                for entry in new_entries:
                    track = Track(
                        id=entry.get('webpage_url') or entry.get('url'),
                        title=entry.get('title', 'Unknown Title'),
                        artist=entry.get('uploader', 'Unknown Artist'),
                        source='soundcloud',
                        duration=int(entry.get('duration', 0)),
                        cover_url=entry.get('thumbnail'),
                        stream_url=None
                    )
                    valid_tracks.append(track)
                    
                    # Если набрали нужное количество — выходим из цикла по записям
                    if len(valid_tracks) >= limit:
                        break

                # Если всё еще не набрали — сдвигаем "окно" поиска дальше
                current_offset += fetch_count
                attempts += 1

            except Exception as e:
                print(f"⚠️ Ошибка при дозагрузке SC: {e}")
                break

        # Возвращаем ровно столько, сколько просили (или сколько удалось найти)
        return valid_tracks[:limit]

    async def get_stream(self, track_id: str) -> str:
        loop = asyncio.get_event_loop()
        
        def extract_url():
            # Для получения стрима используем максимально легкие настройки
            with YoutubeDL(self.common_opts) as ydl:
                info = ydl.extract_info(track_id, download=False)
                if info:
                    return info.get('url')
                return None

        try:
            stream_url = await loop.run_in_executor(None, extract_url)
            if not stream_url:
                print(f"⚠️ Не удалось получить поток для {track_id} (возможно, 404 или Go+)")
                return ""
            return stream_url
        except Exception as e:
            print(f"❌ SC Stream Error: {e}")
            return ""

    async def download(self, track_id: str, output_path: str) -> bool:
        loop = asyncio.get_event_loop()
        
        download_opts = self.common_opts.copy()
        download_opts.update({
            'outtmpl': output_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
        
        def download_track():
            with YoutubeDL(download_opts) as ydl:
                ydl.download([track_id])
        
        try:
            await loop.run_in_executor(None, download_track)
            return True
        except Exception as e:
            print(f"Download failed: {e}")
            return False