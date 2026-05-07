from fastapi import FastAPI
from app.providers.youtube import YouTubeProvider

app = FastAPI(title="SleeWave API")
yt_provider = YouTubeProvider()

@app.get("/search")
async def search(q: str, limit: int = 10, offset: int = 0):
    # Пока просто вызываем напрямую YouTube
    results = await yt_provider.search(q, limit, offset)
    return {"results": results}

@app.get("/stream/{track_id}")
async def get_stream(track_id: str):
    url = await yt_provider.get_stream(track_id)
    return {"url": url}