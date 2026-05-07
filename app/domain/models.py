from pydantic import BaseModel
from typing import Optional

class Track(BaseModel):
    id: str
    title: str
    artist: str
    source: str         # 'vk', 'youtube', 'spotify', 'soundcloud', etc.
    duration: int       # in seconds
    cover_url: Optional[str] = None
    stream_url: Optional[str] = None