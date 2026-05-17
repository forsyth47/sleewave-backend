from __future__ import annotations

import base64
import binascii
import os
import shutil
import tempfile
from pathlib import Path


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg"):
        return
    raise RuntimeError(
        "ffmpeg is not installed or not available in PATH. It is required to convert downloaded audio into MP3."
    )


def ytdlp_auth_options() -> dict:
    options = {}

    cookie_file = os.getenv("SLEEWAVE_YTDLP_COOKIES_FILE")
    cookie_data = os.getenv("SLEEWAVE_YTDLP_COOKIES_BASE64")
    cookies_from_browser = os.getenv("SLEEWAVE_YTDLP_COOKIES_FROM_BROWSER")
    raw_cookie_data = os.getenv("SLEEWAVE_YTDLP_COOKIES")

    if cookie_file:
        options["cookiefile"] = str(Path(cookie_file).expanduser().resolve())
    elif cookie_data:
        options["cookiefile"] = _cookies_file_from_env(cookie_data)
    elif raw_cookie_data:
        options["cookiefile"] = _cookies_file_from_raw_env(raw_cookie_data)
    elif cookies_from_browser:
        options["cookiesfrombrowser"] = _parse_cookies_from_browser(cookies_from_browser)

    return options


def _cookies_file_from_env(cookie_data: str) -> str:
    cookie_path = Path(tempfile.gettempdir()) / "sleewave-ytdlp-cookies.txt"
    cookie_path.write_bytes(_decode_cookie_data(cookie_data))
    cookie_path.chmod(0o600)
    return str(cookie_path)


def _cookies_file_from_raw_env(cookie_data: str) -> str:
    cookie_path = Path(tempfile.gettempdir()) / "sleewave-ytdlp-cookies.txt"
    cookie_path.write_text(cookie_data.replace("\\n", "\n"), encoding="utf-8")
    cookie_path.chmod(0o600)
    return str(cookie_path)


def _decode_cookie_data(cookie_data: str) -> bytes:
    compact_cookie_data = "".join(cookie_data.split())
    try:
        return base64.b64decode(compact_cookie_data, validate=True)
    except binascii.Error:
        return cookie_data.replace("\\n", "\n").encode("utf-8")


def _parse_cookies_from_browser(value: str) -> tuple[str, str | None, str | None, str | None]:
    browser, _, container = value.partition("::")
    browser_and_keyring, _, profile = browser.partition(":")
    browser_name, _, keyring = browser_and_keyring.partition("+")

    return (
        browser_name.strip(),
        profile.strip() or None,
        keyring.strip() or None,
        container.strip() or None,
    )
