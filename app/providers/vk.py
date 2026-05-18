from __future__ import annotations
from http import cookies
from unittest import result

import requests
import json
import time
import base64
import os
import subprocess
import tempfile
from pathlib import Path
import re
from typing import Optional

from app.domain.models import Track
from app.interfaces.music_provider import IMusicProvider
from app.providers.download_helpers import ensure_ffmpeg_available, ytdlp_auth_options


from dotenv import load_dotenv


def _duration_seconds(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class VKProvider(IMusicProvider):
    def __init__(self, cookies: Optional[str] = None):
        self.session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://vk.com/",
            "Origin": "https://vk.com",
        }
        self.session.headers.update(headers)

        load_dotenv()
        cache_root = Path(os.getenv("SLEEWAVE_CACHE_DIR", str(Path(tempfile.gettempdir()) / "sleewave-media-cache")))
        # ensure cache dir exists
        cache_root.mkdir(parents=True, exist_ok=True)

        self.token_cache_file = str(cache_root / "vk_access_token.json")
        self.access_token = None
        self.token_expires = 0
        self.user_id = 0

        if not cookies:
            vk_cookies_path = os.getenv("SLEEWAVE_VK_COOKIES_FROM_BROWSER")
            if vk_cookies_path and os.path.exists(vk_cookies_path):
                try:
                    with open(vk_cookies_path, "r") as f:
                        cookies = f.read().strip()
                        self.cookies = cookies
                except Exception as e:
                    print(f"Failed to read VK cookies from {vk_cookies_path}: {e}")
                    cookies = None

        # print("Initializing VKProvider with cookies:", "Yes" if cookies else "No")
        # print(cookies)
        if cookies:
            self.session.headers.update({"Cookie": cookies})
            token_data = self._load_token_cache()
            now = int(time.time())
            if token_data and "access_token" in token_data and "expires" in token_data:
                if token_data["expires"] > now:
                    self.access_token = token_data["access_token"]
                    self.token_expires = token_data["expires"]
                    self.user_id = token_data["user_id"]
                    print("Loaded access token from cache.")
                else:
                    print("Cached access token expired. Generating new token...")
                    self._generate_and_store_token()
            else:
                print("No cached access token found. Generating new token...")
                self._generate_and_store_token()

    def _load_token_cache(self):
        if os.path.exists(self.token_cache_file):
            try:
                with open(self.token_cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load token cache: {e}")
        return None

    def _save_token_cache(self, access_token, expires, user_id):
        try:
            with open(self.token_cache_file, "w") as f:
                json.dump({"access_token": access_token, "expires": expires, "user_id": user_id}, f)
        except Exception as e:
            print(f"Failed to save token cache: {e}")

    def _generate_and_store_token(self):
        form_data = "version=1&app_id=6287487"
        try:
            resp = self.session.post("https://login.vk.com/?act=web_token", data=form_data, timeout=10)
            print(f"Access token response: {resp.text}")
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
                        print("Access token obtained and cached successfully.")
                    else:
                        print("Failed to extract access token from response.")
                except Exception as e:
                    print(f"Failed to parse access token response as JSON: {e}")
            else:
                print(f"Failed to get access token. HTTP status code: {resp.status_code}")
        except Exception as e:
            print(f"Error while obtaining access token: {e}")

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
                    data = self._i(data, int(args[0]) if args else 0 ^ user_id)
                elif cmd == 'x':
                    data = self._x(data, args[0] if args else "")

            return data if data.startswith(("http://", "https://")) else None

        except Exception as e:
            print(f"Decryption failed: {e}")
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

        # ====================== MAIN API ======================

    def get_direct_url(self, url: str) -> Optional[str]:
        match = re.search(r'audio([-\d]+)_(\d+)', url)
        if match:
            owner_id = int(match.group(1))
            audio_id = int(match.group(2))
            # print(f"Extracted owner_id: {owner_id}, audio_id: {audio_id}")
        else:
            print("Failed to extract owner_id and audio_id from URL.")
            print(f"URL: {url}")
            return None

        try:
            if not hasattr(self, 'access_token'):
                print("Error: access_token is not set. Cannot proceed.")
                return None
            resp = self.session.post(
                "https://api.vk.com/method/audio.getById?v=5.276&client_id=6287487",
                data={
                    "audios": f"{owner_id}_{audio_id}",
                    "access_token": self.access_token
                },
                timeout=40
            )

            # print(resp.text)  # Debug: print raw response content

            if resp.status_code != 200:
                print(f"HTTP Error: {resp.status_code}")
                return None

            # Try to parse JSON directly
            try:
                data = resp.json()
            except Exception as e:
                print(f"Could not parse response as JSON: {e}")
                return None

            # VK API returns 'response' key for success, 'error' for error
            if "error" in data and "access_token has expired" in data["error"].get("error_msg", ""):
                print("Access token expired. Generating new token...")
                self._generate_and_store_token()
                return self.get_direct_url(url)  # Retry with new token
            if 'error' in data:
                print(f"VK API error: {data['error']}")
                return None
            if 'response' in data and isinstance(data['response'], list) and len(data['response']) > 0:
                audio_info = data['response'][0]
                # Try to get direct url
                url = audio_info.get('url')
                # Get other details to embed into mp3 later (must)
                title = audio_info.get('title', 'Unknown Title')
                artist = audio_info.get('artist', 'Unknown Artist')
                thumbnail = audio_info.get('album', {}).get('thumb', {}).get('photo_300', '')
                if url:
                    return url, title, artist, thumbnail
                # If encrypted, try to decrypt
                encrypted = audio_info.get('url')
                if encrypted:
                    return self.decrypt_url(encrypted, owner_id)
                print("No url found in audio info.")
                return None
            print("Unexpected response structure.")
            return None
        except Exception as e:
            print(f"Request error: {e}")
            return None


    async def search(self, query: str, limit: int = 10, offset: int = 0) -> list[Track]:
        resp = self.session.get(
            "https://api.vk.com/method/audio.search",
            params={
                "q": query,
                "count": limit + offset,
                "v": "5.95",
                "access_token": self.access_token,
            },
            timeout=15,
        )

        if "error" in resp and "access_token has expired" in resp["error"].get("error_msg", ""):
                print("Access token expired. Generating new token...")
                self._generate_and_store_token()
                return self.get_direct_url(url)  # Retry with new token
        if 'error' in resp:
            print(f"VK API error: {resp['error']}")
            return None

        # print(resp.text)
        payload = resp.json() if resp.status_code == 200 else {}
        entries = payload.get("response", {}).get("items", []) if isinstance(payload, dict) else []
        print(f"VK search response entries: {len(entries)}")
        results = []

        for entry in entries[offset: offset + limit]:
            if not entry:
                continue

            results.append(
                Track(
                    id=f"{entry.get('owner_id')}_{entry.get('id')}",
                    title=entry.get("title", "Unknown Title"),
                    artist=entry.get("artist", "Unknown Artist"),
                    source="vk",
                    duration=_duration_seconds(entry.get("duration")),
                    cover_url=entry.get("cover_url") or entry.get("thumbnail"),
                    album=entry.get("album") or "VK Music",
                )
            )
        return results

    async def get_stream(self, track_id: str) -> str:
        print(f"track_id: {track_id}")
        url = "https://vk.com/audio"+track_id
        direct_url, title, artist, thumbnail = self.get_direct_url(url=url)
        print(f"Direct URL: {direct_url}")
        if direct_url:
            return direct_url
        else:
            raise RuntimeError("Failed to get direct stream URL from VK.")

    async def download(self, track_id: str, output_path: str) -> Optional[str]:
        print(f"track_id: {track_id}")
        url = "https://vk.com/audio"+track_id
        final_path = Path(output_path)

        direct_url, title, artist, thumbnail = self.get_direct_url(url=url)
        print(f"Direct URL: {direct_url}")
        ensure_ffmpeg_available()
        if not title or not artist:
            output_filename = "vk_audio.mp3"
        else:
            # Clean filename
            clean_title = re.sub(r'[\/:*?"<>|]', '_', title.strip())
            clean_artist = re.sub(r'[\/:*?"<>|]', '_', artist.strip())
            output_filename = f"{clean_artist} - {clean_title}.mp3"
        final_final_path = str(final_path.with_name(output_filename))
        try:
            cmd = ['ffmpeg', '-y']  # -y = overwrite without asking
            cmd.extend(['-i', direct_url])
            if thumbnail:
                cmd.extend(['-i', thumbnail])
            cmd.extend(['-c:a', 'copy'])
            if title:
                cmd.extend(['-metadata', f'title={title}'])
            if artist:
                cmd.extend(['-metadata', f'artist={artist}'])
                cmd.extend(['-metadata', f'album_artist={artist}'])
            if thumbnail:
                cmd.extend(['-map', '0:a:0'])      # Map audio from first input
                cmd.extend(['-map', '1:v:0'])      # Map image from second input
                cmd.extend(['-c:v', 'copy'])
                cmd.extend(['-disposition:v', 'attached_pic'])
            else:
                cmd.extend(['-map', '0:a:0'])

            cmd.extend(['-f', 'mp3', final_final_path])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode == 0:
                return final_final_path
            elif not final_final_path.exists():
                raise RuntimeError("VK download failed: result.stderr")
            return str(final_final_path)
        except Exception as exc:
            raise RuntimeError(f"VK download failed: {exc}") from exc
