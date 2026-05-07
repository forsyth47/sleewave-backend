import os
import tempfile
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC
from app.domain.models import Track
from app.services.music_manager import MusicManager

class DownloadService:
    def __init__(self, music_manager: MusicManager):
        self.music_manager = music_manager
        self.temp_dir = tempfile.gettempdir()

    async def download_to_temp(self, source: str, track: Track) -> str:
        # Создаем временный файл
        temp_file = os.path.join(self.temp_dir, f"{track.id}.mp3")
        
        # Скачиваем трек
        success = await self.music_manager.download(source, track.id, temp_file)
        if not success:
            return None
        
        # Добавляем метаданные
        self._add_metadata(temp_file, track)
        
        return temp_file

    def _add_metadata(self, file_path: str, track: Track):
        try:
            # Загружаем файл
            audio = MP3(file_path, ID3=ID3)
            
            # Создаем теги, если их нет
            if audio.tags is None:
                audio.add_tags()
            
            # Добавляем метаданные
            audio.tags.add(TIT2(encoding=3, text=track.title))
            audio.tags.add(TPE1(encoding=3, text=track.artist))
            if track.cover_url:
                # Для обложки нужно скачать изображение, но пока пропустим
                pass
            
            # Сохраняем
            audio.save()
        except Exception as e:
            print(f"Failed to add metadata: {e}")