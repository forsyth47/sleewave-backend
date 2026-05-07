from app.providers.youtube import YouTubeProvider

class SoundCloudProvider(YouTubeProvider):
    def __init__(self):
        super().__init__()
        self.ydl_opts['default_search'] = 'scsearch' # Меняем поиск на SoundCloud

    # Метод search и get_stream унаследуются от YouTubeProvider, 
    # так как логика yt-dlp для них идентична. 
    # Это чистый ООП в действии.
    
    async def search(self, query: str, limit: int = 10, offset: int = 0):
        tracks = await super().search(query, limit, offset)
        for track in tracks:
            track.source = 'soundcloud'
        return tracks