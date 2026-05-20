from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL
from app.providers.download_helpers import ytdlp_auth_options

import httpx

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider
from app.providers.download_helpers import ensure_ffmpeg_available


logger = logging.getLogger(__name__)


def _duration_seconds(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class VKProvider(IMusicProvider):
    def __init__(self, cookies: Optional[str] = None):
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://vk.com/",
                "Origin": "https://vk.com",
            },
            timeout=httpx.Timeout(40.0, connect=10.0),
            follow_redirects=True,
        )

        cache_root = Path(os.getenv("SLEEWAVE_CACHE_DIR", str(Path(tempfile.gettempdir()) / "sleewave-media-cache")))

        self.token_cache_file = str(cache_root / "vk_access_token.json")
        self.access_token = None
        self.token_expires = 0
        self.user_id = 0
        self.cookies = cookies

        if not cookies:
            vk_cookies_path = os.getenv("SLEEWAVE_VK_COOKIES_FILE")
            if vk_cookies_path and os.path.exists(vk_cookies_path):
                try:
                    with open(vk_cookies_path, "r") as f:
                        cookies = f.read().strip()
                        self.cookies = cookies
                except Exception as e:
                    logger.warning("Failed to read VK cookies from %s: %s", vk_cookies_path, e)
                    cookies = None

        if cookies:
            self.client.headers.update({"Cookie": cookies})
            token_data = self._load_token_cache()
            now = int(time.time())
            if token_data and "access_token" in token_data and "expires" in token_data:
                if token_data["expires"] > now:
                    self.access_token = token_data["access_token"]
                    self.token_expires = token_data["expires"]
                    self.user_id = token_data.get("user_id", 0)
                    logger.debug("Loaded access token from cache.")
                else:
                    logger.debug("Cached access token expired. Will generate a new token on demand.")
            else:
                logger.debug("No cached access token found. Will generate a new token on demand.")

    def _load_token_cache(self):
        if os.path.exists(self.token_cache_file):
            try:
                with open(self.token_cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load token cache: %s", e)
        return None

    def _save_token_cache(self, access_token, expires, user_id):
        try:
            with open(self.token_cache_file, "w") as f:
                json.dump({"access_token": access_token, "expires": expires, "user_id": user_id}, f)
        except Exception as e:
            logger.warning("Failed to save token cache: %s", e)

    async def _generate_and_store_token(self) -> bool:
        try:
            resp = await self.client.post(
                "https://login.vk.com/?act=web_token",
                data={"version": "1", "app_id": "6287487"},
            )
            logger.debug("VK access token response received: status=%s", resp.status_code)
            if resp.status_code == 200:
                try:
                    resp_json = resp.json()
                    access_token = None
                    expires = 0
                    if "access_token" in resp_json:
                        access_token = resp_json["access_token"]
                        expires = resp_json.get("expires", 0)
                        user_id = resp_json.get("user_id", 0)
                    elif "data" in resp_json and isinstance(resp_json["data"], dict):
                        access_token = resp_json["data"].get("access_token")
                        expires = resp_json["data"].get("expires", 0)
                        user_id = resp_json["data"].get("user_id", 0)
                    if access_token and expires:
                        self.access_token = access_token
                        self.token_expires = expires
                        self.user_id = user_id
                        self._save_token_cache(access_token, expires, user_id)
                        logger.debug("Access token obtained and cached successfully.")
                        return True
                    else:
                        logger.warning("Failed to extract access token from response.")
                except Exception as e:
                    logger.warning("Failed to parse access token response as JSON: %s", e)
            else:
                logger.warning("Failed to get access token. HTTP status code: %s", resp.status_code)
        except Exception as e:
            logger.warning("Error while obtaining access token: %s", e)
        return False

    async def _ensure_access_token(self) -> bool:
        now = int(time.time())
        if self.access_token and self.token_expires > now:
            return True

        token_data = self._load_token_cache()
        if token_data and token_data.get("access_token") and token_data.get("expires", 0) > now:
            self.access_token = token_data["access_token"]
            self.token_expires = token_data["expires"]
            self.user_id = token_data.get("user_id", 0)
            return True

        if not self.cookies:
            return False

        return await self._generate_and_store_token()

    # ====================== DECRYPTION ======================

    def _b64(self, s: str) -> str:
        if not s or len(s) % 4 == 1:
            return ""
        try:
            return base64.b64decode(s + '==').decode('utf-8', errors='ignore')
        except:
            return ""

    def decrypt_url(self, encrypted: str, user_id: int = 0) -> Optional[str]:
        if not encrypted or "audio_api_unavailable" not in encrypted:
            return encrypted

        try:
            extra = encrypted.split("?extra=")[1].split("#")
            data = self._b64(extra[0])
            key_str = self._b64(extra[1]) if len(extra) > 1 else ""

            transforms = key_str.split("\t") if key_str else []

            for t in reversed(transforms):
                if not t:
                    continue
                cmd = t[0]
                args = t[1:].split("\v") if len(t) > 1 else []

                if cmd == 'v':
                    data = data[::-1]
                elif cmd == 'r':
                    data = self._r(data, int(args[0]) if args else 0)
                elif cmd == 's':
                    data = self._s(data, int(args[0]) if args else 0)
                elif cmd == 'i':
                    data = self._i(data, (int(args[0]) if args else 0) ^ user_id)
                elif cmd == 'x':
                    data = self._x(data, args[0] if args else "")

            return data if data.startswith(("http://", "https://")) else None

        except Exception as e:
            logger.warning("Decryption failed: %s", e)
            return None

    def _r(self, s: str, shift: int) -> str:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN0PQRSTUVWXYZO123456789+/="
        chars = list(s)
        for i in range(len(chars)-1, -1, -1):
            pos = alphabet.find(chars[i])
            if pos != -1:
                chars[i] = alphabet[(pos - shift) % len(alphabet)]
        return ''.join(chars)

    def _s(self, s: str, shift: int) -> str:
        chars = list(s)
        n = len(chars)
        for i in range(n-1, 0, -1):
            j = (shift * (i + 1)) % (i + 1)
            chars[i], chars[j] = chars[j], chars[i]
        return ''.join(chars)

    def _i(self, s: str, key: int) -> str:
        return ''.join(chr(ord(c) ^ key) for c in s)

    def _x(self, s: str, key: str) -> str:
        if not key:
            return s
        k = ord(key[0])
        return ''.join(chr(ord(c) ^ k) for c in s)

    async def get_direct_url(self, url: str) -> Optional[tuple[str, str, str, str]]:
        match = re.search(r'audio([-\d]+)_(\d+)', url)
        if match:
            owner_id = int(match.group(1))
            audio_id = int(match.group(2))
        else:
            logger.warning("Failed to extract owner_id and audio_id from URL: %s", url)
            return None

        try:
            if not await self._ensure_access_token():
                logger.error("access_token is not available. Cannot proceed.")
                return None

            resp = await self.client.post(
                "https://api.vk.com/method/audio.getById?v=5.276&client_id=6287487",
                data={
                    "audios": f"{owner_id}_{audio_id}",
                    "access_token": self.access_token
                },
            )

            if resp.status_code != 200:
                logger.warning("HTTP Error: %s", resp.status_code)
                return None

            # Try to parse JSON directly
            try:
                data = resp.json()
            except Exception as e:
                logger.warning("Could not parse response as JSON: %s", e)
                return None

            # VK API returns 'response' key for success, 'error' for error
            if "error" in data and "access_token has expired" in data["error"].get("error_msg", ""):
                logger.debug("Access token expired. Generating new token...")
                if await self._generate_and_store_token():
                    return await self.get_direct_url(url)
                return None
            if 'error' in data:
                logger.warning("VK API error: %s", data['error'])
                return None
            if 'response' in data and isinstance(data['response'], list) and len(data['response']) > 0:
                audio_info = data['response'][0]
                url = audio_info.get('url')
                # Get other details to embed into mp3 later (must)
                title = audio_info.get('title', 'Unknown Title')
                artist = audio_info.get('artist', 'Unknown Artist')
                album_info = audio_info.get('album')
                thumbnail = (
                    album_info.get('thumb', {}).get('photo_300', '')
                    if isinstance(album_info, dict)
                    else ''
                )
                if url:
                    return url, title, artist, thumbnail
                logger.warning("No url found in audio info.")
                return None
            logger.warning("Unexpected response structure.")
            return None
        except Exception as e:
            logger.warning("Request error: %s", e)
            return None


    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        if not await self._ensure_access_token():
            logger.warning("Skipping VK search because no access token is available.")
            return []

        resp = await self.client.get(
            "https://api.vk.com/method/audio.search",
            params={
                "q": query,
                "count": limit + offset,
                "v": "5.95",
                "access_token": self.access_token,
            },
        )

        payload = resp.json() if resp.status_code == 200 else {}
        if isinstance(payload, dict) and "error" in payload:
            error = payload["error"]
            if "access_token has expired" in error.get("error_msg", ""):
                logger.debug("Access token expired. Generating new token...")
                await self._generate_and_store_token()
                return await self.search(query, limit=limit, offset=offset)
            logger.warning("VK API error: %s", error)
            return []

        entries = payload.get("response", {}).get("items", []) if isinstance(payload, dict) else []
        # logger.debug("VK search response entries: %s", len(entries))
        results = []

        for entry in entries[offset: offset + limit]:
            if not entry:
                continue

            # Extract album title if it's a dict, otherwise use as string
            album_obj = entry.get("album")
            album_name = album_obj.get("title", "VK Music") if isinstance(album_obj, dict) else (album_obj or "VK Music")

            results.append(
                Track(
                    id=f"{entry.get('owner_id')}_{entry.get('id')}",
                    title=entry.get("title", "Unknown Title"),
                    artist=entry.get("artist", "Unknown Artist"),
                    source="vk",
                    duration=_duration_seconds(entry.get("duration")),
                    cover_url=entry.get("cover_url") or entry.get("thumbnail"),
                    album=album_name,
                )
            )
        return results

    async def get_stream(self, track_id: str) -> str:
        url = "https://vk.com/audio"+track_id
        direct_url_info = await self.get_direct_url(url=url)
        if not direct_url_info:
            raise RuntimeError("Failed to get direct stream URL from VK.")

        direct_url, _, _, _ = direct_url_info
        return direct_url

    async def download(
        self,
        track_id: str,
        output_path: str,
        stream_url: Optional[str] = None,
    ) -> Optional[str]:
        url = "https://vk.com/audio"+track_id
        final_path = Path(output_path)

        direct_url = stream_url
        if not direct_url:
            direct_url_info = await self.get_direct_url(url=url)
            if not direct_url_info:
                raise RuntimeError("Failed to get direct download URL from VK.")
            direct_url, title, artist, thumbnail = direct_url_info
        else:
            title = artist = thumbnail = None
        # Prefer yt-dlp for VK HLS/m3u8 streams (faster and handles fragments).
        # Keep the old ffmpeg-based subprocess flow commented out for future reference.
        # ensure_ffmpeg_available()  # yt-dlp postprocessors still require ffmpeg

        # Build a safe output template based on requested final_path
        output_template = str(final_path.with_suffix(".%(ext)s"))

        ytdlp_options = {
            **ytdlp_auth_options(),
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
            'http_chunk_size': 5242880, # 5MB
            'remote_components': {'ejs:github'},
            'concurrent_fragment_downloads': 20,
            "hls_prefer_native": True,
            'retries': 3,
            'socket_timeout': 10,
            'fragment_retries': 3,
            'buffersize': 1024 * 1024 * 64,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            # "postprocessor_args": [],
            "final_ext": "mp3",
        }

        def ytdlp_download() -> str:
            # Use yt-dlp to download/convert; it handles m3u8/fragments efficiently.
            # We still prefer to ensure ffmpeg is available for postprocessing.
            ensure_ffmpeg_available()
            # Add some metadata via postprocessing is handled by yt-dlp if available.
            with YoutubeDL(ytdlp_options) as ydl:
                info = ydl.extract_info(direct_url, download=True)

                downloaded_path = Path(ydl.prepare_filename(info)).with_suffix(".mp3")

            return str(downloaded_path)

        # --- Legacy ffmpeg subprocess approach (commented out) ---
        # def run_download() -> str:
        #     cmd = ['ffmpeg', '-y']  # -y = overwrite without asking
        #     cmd.extend(['-i', direct_url])
        #     if thumbnail:
        #         cmd.extend(['-i', thumbnail])
        #     cmd.extend(['-c:a', 'copy'])
        #     if title:
        #         cmd.extend(['-metadata', f'title={title}'])
        #     if artist:
        #         cmd.extend(['-metadata', f'artist={artist}'])
        #         cmd.extend(['-metadata', f'album_artist={artist}'])
        #     if thumbnail:
        #         cmd.extend(['-map', '0:a:0'])
        #         cmd.extend(['-map', '1:v:0'])
        #         cmd.extend(['-c:v', 'copy'])
        #         cmd.extend(['-disposition:v', 'attached_pic'])
        #     else:
        #         cmd.extend(['-map', '0:a:0'])
        #     cmd.extend(['-f', 'mp3', str(output_file_path)])
        #     result = subprocess.run(cmd, capture_output=False, text=True, timeout=180)
        #     if result.returncode != 0:
        #         raise RuntimeError(f"VK download failed: {result.stderr}")
        #     if not output_file_path.exists():
        #         raise RuntimeError("VK download finished without creating the expected output file.")
        #     return str(output_file_path)

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, ytdlp_download)
        except Exception as exc:
            raise RuntimeError(f"VK download (yt-dlp) failed: {exc}") from exc
