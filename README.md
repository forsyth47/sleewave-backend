# Sleewave Backend

Sleewave Backend is a local media service for a Flutter client. It aggregates multiple music sources, deduplicates matching tracks, prepares cache-backed playback, and serves downloads for the mobile app.

## Features

- Source discovery endpoint for the client UI.
- Search across one source, several sources, or all available sources.
- Track deduplication across providers.
- Search prioritization for tracks already cached on the server or already saved on the device.
- Temporary MP3 cache with size-based eviction and least-recently-used cleanup.
- Local stream and download URLs backed by server cache.
- Device library sync so the backend can avoid offering duplicate tracks again.
- Structured JSON error responses.

## Current source status

- `ytm` - available
- `yt` - available
- `sc` - available
- `spotify` - listed but disabled until integration is implemented
- `vk` - listed but disabled until integration is implemented

## API overview

### `GET /sources`

Returns the list of sources with availability flags so the Flutter app can render a source picker.

### `GET /search`

Query parameters:

- `q` - search text
- `sources` - comma-separated source ids such as `ytm,yt,sc`
- `source` - optional single-source alias for compatibility
- `limit` - result count, default `10`
- `offset` - pagination offset, default `0`
- `device_id` - optional device identifier used to prioritize already saved tracks

Examples:

```http
GET /search?q=daft%20punk&sources=all&device_id=phone-01
GET /search?q=daft%20punk&sources=ytm,sc
GET /search?q=daft%20punk&source=yt
```

Each result contains:

- `result_id` - opaque token that identifies the selected search result for a short time
- primary source and track id
- alternate source references for duplicates merged from other providers
- `track_key` for stable client-side identity
- availability flags: `in_server_cache`, `on_device`, `preferred_origin`

### `POST /stream`

Prepares a track for playback. If the track is already cached, the backend reuses it. Otherwise it downloads the file to the temporary cache first.

Request body:

```json
{
  "result_id": "QvQ0S4VixjP2"
}
```

`result_id` comes from the latest `/search` response. It is intentionally short-lived, so the client should use it soon after the user picks a track.

Response:

```json
{
  "track": { "...": "..." },
  "cache_key": "server-cache-key",
  "cache_hit": true,
  "stream_url": "/media/server-cache-key/stream",
  "download_url": "/media/server-cache-key/download"
}
```

### `POST /download`

Uses the same `result_id` request body as `/stream` and prepares a cached file for device download.

### `GET /media/{cache_key}/stream`

Streams the cached MP3 file from the local backend.

### `GET /media/{cache_key}/download`

Downloads the cached MP3 file from the local backend.

### `POST /device-library/sync`

Replaces the known track list for a device and removes matching tracks from the server cache because the device already owns them.

Request body:

```json
{
  "device_id": "phone-01",
  "tracks": [
    {
      "title": "Track Title",
      "artist": "Artist Name",
      "duration": 210
    }
  ]
}
```

### `POST /device-library/confirm-download`

Call this after the Flutter app has finished saving a downloaded track. The backend adds the track to the device library and removes the matching server cache entry.

## Error format

All handled errors return JSON in this shape:

```json
{
  "error": {
    "code": "provider_unavailable",
    "message": "Spotify integration has not been implemented yet.",
    "details": {
      "source": "spotify"
    }
  }
}
```

## Cache behavior

- Cache location defaults to the OS temp directory inside `sleewave-media-cache`.
- Files are stored as MP3.
- When the cache grows over the configured limit, the oldest unused tracks are evicted first.
- When a track is confirmed as saved on a device, the server cache copy is removed.

Environment variables:

- `SLEEWAVE_CACHE_DIR` - optional custom cache directory
- `SLEEWAVE_CACHE_MAX_MB` - maximum cache size in megabytes, default `1024`
- `SLEEWAVE_SEARCH_RESULT_TTL_SECONDS` - how long a search `result_id` stays valid, default `1800`

## Run locally

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Make sure `ffmpeg` is installed and available in `PATH`. `yt-dlp` uses it to convert audio into MP3 files.

3. Start the API:

```bash
uvicorn app.main:app --reload
```

The backend will be available at [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Notes

- This repository is ready for Flutter integration, but the Flutter client still needs to call `/device-library/sync` or `/device-library/confirm-download` so the backend can suppress duplicates correctly.
- The intended flow is `search -> user picks a result_id -> stream/download -> confirm device download when saved locally`.
- `Spotify` and `VK` are exposed to the client as disabled sources so the UI can show future integrations without pretending they work today.
