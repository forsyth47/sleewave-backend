# Sleewave Backend

Sleewave Backend is a local media service for a Flutter client. It aggregates multiple music sources, deduplicates matching tracks, prepares cache-backed playback, and serves downloads for the mobile app.

## Features

- Source discovery endpoint for the client UI.
- Search across one source, several sources, or all available sources.
- Track deduplication across providers.
- Search prioritization for tracks already cached on the server or already saved on the device.
- Temporary MP3 cache with size-based eviction and least-recently-used cleanup.
- Direct stream and download endpoints backed by server cache.
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

Streams search results as server-sent events so the client can render tracks as they are discovered instead of waiting for the whole search response.

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

Events are sent in this shape:

```sse
event: start
data: {"event":"start","query":"daft punk","sources":["ytm","yt","sc"],"emitted":0}

event: track
data: {"event":"track","source":"ytm","track":{"title":"One More Time","artist":"Daft Punk","duration":320,"cover_url":"https://...","album":"Discovery","result_id":"QvQ0S4VixjP2","track_key":"stable-exact-key","base_track_key":"stable-title-artist-key","availability":{"in_server_cache":true,"preferred_origin":"server"}},"emitted":1}

event: warning
data: {"event":"warning","source":"sc","warning":{...},"emitted":4}

event: done
data: {"event":"done","emitted":10}
```

Use `result_id` immediately for stream/download actions. Store `track_key` and `base_track_key` as stable identity values for duplicate detection.

### `POST /stream/{result_id}`

Downloads the selected result into the server temp cache if needed, then returns the cached MP3 inline for playback. If the server already has a matching `track_key` or `base_track_key`, it reuses the cached file instead of downloading from the provider again.

```http
POST /stream/QvQ0S4VixjP2
```

`result_id` is short-lived. If it expires, search again and use the fresh `result_id`.

### `POST /download/{result_id}`

Downloads the selected result into the server temp cache if needed, then returns the cached MP3 as an attachment for the device to save.

```http
POST /download/QvQ0S4VixjP2?device_id=phone-01
```

When `device_id` is provided, the backend checks that phone's library first. If the track is already on that phone, it returns a structured `409 track_already_on_device` error and does not send the file again.

### `GET /saved-songs`

Returns every song currently available in the backend download cache, newest recently accessed first.
Each saved song is registered as a temporary search result, so the returned `result_id` works with the normal `/stream/{result_id}` and `/download/{result_id}` endpoints.

```http
GET /saved-songs
```

Response:

```json
{
  "songs": [
    {
      "title": "One More Time",
      "artist": "Daft Punk",
      "duration": 320,
      "cover_url": "https://...",
      "album": "Discovery",
      "result_id": "QvQ0S4VixjP2",
      "track_key": "stable-exact-key",
      "base_track_key": "stable-title-artist-key",
      "availability": {
        "in_server_cache": true,
        "cache_key": "stable-exact-key",
        "preferred_origin": "server"
      }
    }
  ],
  "count": 1
}
```

Use the returned `result_id` exactly like a search result:

```http
POST /stream/QvQ0S4VixjP2
POST /download/QvQ0S4VixjP2
```

Cached matches are also emitted first from `GET /search` before provider searches complete. If the cached matches fill the requested `limit`, the backend returns them without searching remote providers.

### `POST /device-library/sync`

Replaces the known track list for a device. Server cache is retained so other phones can reuse already downloaded files.

Request body:

```json
{
  "device_id": "phone-01",
  "tracks": [
    {
      "track_key": "stable-exact-key",
      "base_track_key": "stable-title-artist-key"
    }
  ]
}
```

### `POST /device-library/confirm-download`

Call this after the Flutter app has finished saving a downloaded track. The backend adds the track to the device library and keeps the server cache available for reuse.

Prefer stable keys from the search result:

```json
{
  "device_id": "phone-01",
  "track_key": "stable-exact-key",
  "base_track_key": "stable-title-artist-key"
}
```

`result_id` is still accepted as a fallback while the search token is valid.

```json
{
  "device_id": "phone-01",
  "result_id": "QvQ0S4VixjP2"
}
```

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
- When a track is confirmed as saved on a device, the server cache copy is retained for other devices until normal cache eviction removes it.

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
