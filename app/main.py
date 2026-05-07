from fastapi import FastAPI
from app.services.music_manager import MusicManager

app = FastAPI()
manager = MusicManager()

@app.get("/search")
async def search(source: str, q: str, limit: int = 10, offset: int = 0):
    results = await manager.search(source, q, limit, offset)
    return {"results": results}