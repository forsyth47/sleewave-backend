from __future__ import annotations

import shutil


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg"):
        return
    raise RuntimeError(
        "ffmpeg is not installed or not available in PATH. It is required to convert downloaded audio into MP3."
    )
