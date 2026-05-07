from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.services.music_manager import MusicManager
from app.services.download_service import DownloadService

app = FastAPI()

# Добавляем CORS для Flutter
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажи конкретные origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = MusicManager()
download_service = DownloadService(manager)

@app.get("/search")
async def search(source: str, q: str, limit: int = 10, offset: int = 0):
    results = await manager.search(source, q, limit, offset)
    return {"results": results}

@app.get("/stream")
async def stream(source: str, track_id: str):
    stream_url = await manager.get_stream(source, track_id)
    return {"stream_url": stream_url}

@app.post("/download")
async def download(source: str, track_id: str, output_path: str):
    success = await manager.download(source, track_id, output_path)
    return {"success": success}

@app.post("/download_temp")
async def download_temp(source: str, track_id: str, title: str, artist: str):
    # Создаем объект Track для метаданных
    track = Track(
        id=track_id,
        title=title,
        artist=artist,
        source=source,
        duration=0  # Можно добавить позже
    )
    temp_path = await download_service.download_to_temp(source, track)
    return {"temp_path": temp_path}