# Sleewave Backend API Docs

This backend is a local music aggregation and caching API for a player app. It searches multiple providers, deduplicates matching tracks, downloads tracks into a server-side MP3 cache, streams cached audio, and keeps a lightweight record of tracks saved on each device.

Base URL in local development:

```text
http://127.0.0.1:8000
```

## Core Concepts

### Result IDs

`result_id` is the main handle your player should use for playback and downloads.

- It is returned by `/search` and `/saved-songs`.
- It is stable and permanent for a track.
- Use it with:

```http
GET /stream/{result_id}
GET /download/{result_id}
```

If a `result_id` is unknown, the backend returns `404 search_result_not_found`. Search again or call `/saved-songs` to register the track in the backend catalog.

### Internal Track Keys

The backend still keeps `track_key` and `base_track_key` internally for duplicate detection.

- `track_key` includes title, artist, and approximate duration.
- `base_track_key` includes title and artist only.
- `result_id` is the only public ID the player needs to store or send.

### Server Cache

When `/stream/{result_id}` or `/download/{result_id}` is called, the backend prepares an MP3 in the server cache.

- If the same song is already cached, it reuses the existing MP3.
- Cached songs appear first in search results when they match the query.
- `/saved-songs` lists every cached song with its permanent `result_id`.
- Cache is temporary and can be evicted by size limit.

### Device Library

The device library is not the audio files themselves. It is a list of stable track keys for a specific device.

Use it to tell the backend which tracks are already saved locally on the phone, so search results can show `availability.on_device = true` and `/download/{result_id}?device_id=...` can avoid sending duplicates.

## Recommended Player Flows

### Search And Play

1. Call `/search?q=...`.
2. Listen to SSE events.
3. Render every `event: track`.
4. Use `track.result_id` to play:

```http
GET /stream/{result_id}
```

The response is `audio/mpeg`, inline.

### Search And Download

1. Call `/search?q=...&device_id=phone-01`.
2. Let the user choose a track.
3. Call:

```http
GET /download/{result_id}?device_id=phone-01
```

4. Save the MP3 on the device.
5. After the app confirms the file is saved, call `/device-library/confirm-download`.

Recommended confirmation payload:

```json
{
  "device_id": "phone-01",
  "result_id": "stable-exact-key"
}
```

### Open The Saved/Downloaded Songs Screen

Call:

```http
GET /saved-songs
```

The response contains cached songs with permanent `result_id`s. Use those IDs exactly like search results:

```http
GET /stream/{result_id}
GET /download/{result_id}
```

### Search With Instant Cached Results

When you call `/search`, the backend checks the server cache first. Matching cached songs are emitted immediately before remote provider searches finish.

If cached matches fill the requested `limit`, the backend returns them and skips remote provider searches.

This means your player can use one search UI for both local cached songs and online results.

## Endpoints

### `GET /health`

Simple health check. Hidden from schema.

Response:

```json
{
  "status": "ok"
}
```

### `GET /sources`

Returns available and disabled providers.

Response:

```json
{
  "sources": [
    {
      "id": "ytm",
      "name": "YouTube Music",
      "available": true,
      "supports_search": true,
      "supports_stream": true,
      "supports_download": true
    }
  ]
}
```

Current sources:

- `ytm` - YouTube Music, available
- `yt` - YouTube, available
- `sc` - SoundCloud, available
- `spotify` - disabled placeholder
- `vk` - disabled placeholder

### `GET /search`

Streams search results as server-sent events.

Query parameters:

- `q` - required search text, minimum length 1
- `sources` - optional comma-separated provider IDs, for example `ytm,yt,sc`
- `source` - optional single-provider alias
- `limit` - default `10`, minimum `1`, maximum `100`
- `offset` - default `0`, minimum `0`
- `device_id` - optional device ID for device availability annotation

Examples:

```http
GET /search?q=daft%20punk
GET /search?q=daft%20punk&sources=ytm,sc
GET /search?q=daft%20punk&source=yt
GET /search?q=daft%20punk&device_id=phone-01
```

SSE events:

```sse
event: start
data: {"event":"start","query":"daft punk","sources":["ytm","yt","sc"],"emitted":0}

event: track
data: {"event":"track","source":"ytm","track":{"title":"One More Time","artist":"Daft Punk","duration":320,"cover_url":"https://...","album":"Discovery","result_id":"stable-exact-key","availability":{"in_server_cache":true,"cache_key":"stable-exact-key","preferred_origin":"server"}},"emitted":1}

event: warning
data: {"event":"warning","source":"sc","warning":{"source":"sc","message":"Search failed for source 'sc' and it was skipped."},"emitted":4}

event: done
data: {"event":"done","emitted":10}
```

Track response fields:

```json
{
  "title": "One More Time",
  "artist": "Daft Punk",
  "duration": 320,
  "cover_url": "https://...",
  "album": "Discovery",
  "result_id": "stable-exact-key",
  "availability": {
    "in_server_cache": true,
    "on_device": false,
    "cache_key": "stable-exact-key",
    "preferred_origin": "server"
  }
}
```

Availability meanings:

- `in_server_cache`: the backend already has an MP3 cached.
- `on_device`: the supplied `device_id` already has this track in its library.
- `cache_key`: server cache key when cached.
- `preferred_origin`: `device`, `server`, or `remote`.

Client handling:

- Render `track` events as they arrive.
- Show cached/device tracks first if they arrive first.
- Treat `warning` events as non-fatal if searching multiple sources.
- Treat a provider error as fatal only when searching one source and the request fails.

### `GET /stream/{result_id}`

Prepares the selected track in the server cache if needed, then streams the MP3 inline.

```http
GET /stream/stable-exact-key
```

Response:

- Content type: `audio/mpeg`
- Header: `Content-Disposition: inline; filename="Artist - Title.mp3"`

Use this for immediate playback in the player.

Important:

- This may take time on first play because the backend downloads and converts the source audio.
- Later plays are fast because they reuse the cache.
- If the track ID is unknown, search again or call `/saved-songs` again.

### `GET /download/{result_id}`

Prepares the selected track in the server cache if needed, then returns the MP3 as an attachment.

```http
GET /download/stable-exact-key
GET /download/stable-exact-key?device_id=phone-01
```

Response:

- Content type: `audio/mpeg`
- Header: `Content-Disposition: attachment; filename="Artist - Title.mp3"`

When `device_id` is provided, the backend checks that device's library before sending the file. If the device already has it, the backend returns:

```json
{
  "error": {
    "code": "track_already_on_device",
    "message": "This track is already saved on the device.",
    "details": {
      "device_id": "phone-01",
      "result_id": "stable-exact-key"
    }
  }
}
```

Client handling:

- For `409 track_already_on_device`, do not show a scary error. Show “Already downloaded” or switch to local playback.
- After a successful download and local save, call `/device-library/confirm-download`.

### `GET /saved-songs`

Lists songs currently in the server cache, newest recently accessed first. Each song is returned as a normal playable search result with its permanent `result_id`.

```http
GET /saved-songs?limit=50&offset=0
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
      "result_id": "stable-exact-key",
      "availability": {
        "in_server_cache": true,
        "on_device": false,
        "cache_key": "stable-exact-key",
        "preferred_origin": "server"
      }
    }
  ],
  "count": 1,
  "total": 1,
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```

Client handling:

- Use `result_id` with `/stream/{result_id}` and `/download/{result_id}`.
- Increment `offset` by `count` when `has_more = true`.
- If this list is empty, there are no server-cached songs yet.

### `DELETE /tracks/{result_id}`

Deletes one track from the server cache/catalog. Use this when the user removes a server-cached song.

```http
DELETE /tracks/stable-exact-key
```

Response:

```json
{
  "result_id": "stable-exact-key",
  "cache_deleted": true
}
```

### `DELETE /cache`

Deletes all cached MP3 files and resets the cache index. The track catalog and device library are kept.

```http
DELETE /cache
```

Response:

```json
{
  "deleted_count": 12,
  "track_catalog_cleared": false,
  "device_library_cleared": false
}
```

### `DELETE /server-temp`

Clears all backend temporary state: cached MP3s, track catalog, and device library.

```http
DELETE /server-temp
```

Response:

```json
{
  "deleted_count": 12,
  "track_catalog_cleared": true,
  "device_library_cleared": true
}
```

### `POST /device-library/sync`

Replaces the backend's known track list for a device.

Use this when the app starts, after scanning local downloads, or after restoring app state.

Request:

```json
{
  "device_id": "phone-01",
  "tracks": [
    {
      "result_id": "stable-exact-key"
    }
  ]
}
```

Response:

```json
{
  "device_id": "phone-01",
  "track_count": 1,
  "server_cache_retained": true
}
```

Important:

- This replaces all known tracks for that device.
- It does not delete server cache files.
- Send every locally saved `result_id` you know about.

### `POST /device-library/confirm-download`

Adds one track to the backend's known library for a device.

Use this after the player successfully saves a downloaded MP3 locally.

```json
{
  "device_id": "phone-01",
  "result_id": "stable-exact-key"
}
```

Response:

```json
{
  "device_id": "phone-01",
  "registered": true,
  "server_cache_retained": true
}
```

`registered` is `false` if the device library already had the track.

## Error Format

All handled errors use:

```json
{
  "error": {
    "code": "error_code",
    "message": "Human readable message.",
    "details": {}
  }
}
```

Common errors:

| HTTP | Code | Meaning | Client Handling |
| --- | --- | --- | --- |
| 400 | `bad_request` | Invalid logical request, such as empty query after trimming. | Fix request or show validation message. |
| 404 | `provider_not_found` | Unknown source ID. | Remove source or refresh `/sources`. |
| 404 | `search_result_not_found` | `result_id` does not exist in the track catalog. | Search again or call `/saved-songs` again. |
| 404 | `cache_entry_not_found` | Cached file was missing. | Refresh saved songs or search again. |
| 409 | `track_already_on_device` | Device already has this song. | Show already downloaded/local state. |
| 422 | `validation_error` | Request body/query failed FastAPI validation. | Fix payload. |
| 502 | `track_preparation_failed` | Download/conversion failed. | Show retry, possibly try another source. |
| 503 | `provider_unavailable` | Provider disabled or failed. | Show source unavailable or fallback to other sources. |
| 500 | `internal_server_error` | Unexpected backend error. | Show generic retry/error. |

## Source And Provider Notes

The backend exposes providers through `/sources`.

Available providers:

- `ytm`: YouTube Music search, download through `yt-dlp`, MP3 conversion.
- `yt`: YouTube search, download through `yt-dlp`, MP3 conversion.
- `sc`: SoundCloud search, download through `yt-dlp`, MP3 conversion.

Disabled placeholders:

- `spotify`: visible but unavailable until implemented.
- `vk`: visible but unavailable until implemented.

Downloads require `ffmpeg` in `PATH`, because `yt-dlp` uses it to convert audio to MP3.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SLEEWAVE_CACHE_DIR` | OS temp dir + `sleewave-media-cache` | Stores cached MP3s, cache index, track catalog, and device library. |
| `SLEEWAVE_CACHE_MAX_MB` | `1024` | Maximum server cache size in MB. |
| `SLEEWAVE_YTDLP_COOKIES` | unset | Raw Netscape cookies.txt content for `yt-dlp` authentication. |
| `SLEEWAVE_YTDLP_COOKIES_BASE64` | unset | Base64-encoded Netscape cookies.txt content for `yt-dlp` authentication. |
| `SLEEWAVE_YTDLP_COOKIES_FILE` | unset | Path to a Netscape cookies.txt file for `yt-dlp` authentication. |
| `SLEEWAVE_YTDLP_COOKIES_FROM_BROWSER` | unset | Browser cookie source for `yt-dlp`, for example `chrome`, `firefox:default`, or `chrome:Profile 1`. |

Cookie env precedence is `SLEEWAVE_YTDLP_COOKIES_FILE`, then `SLEEWAVE_YTDLP_COOKIES_BASE64`, then `SLEEWAVE_YTDLP_COOKIES`, then `SLEEWAVE_YTDLP_COOKIES_FROM_BROWSER`.

## Client Implementation Notes

### Player State

Recommended client states:

- `remote`: track is from provider and not cached.
- `server_cached`: `availability.in_server_cache = true`.
- `device_saved`: `availability.on_device = true`.

Use `availability.preferred_origin` to choose the best default:

- `device`: play the local file if the app has it.
- `server`: play through `GET /stream/{result_id}`.
- `remote`: call `GET /stream/{result_id}` and allow the backend to download/cache first.

### Download Button Behavior

- If `availability.on_device = true`, show “Downloaded”.
- If `availability.in_server_cache = true`, download should be fast because the server already has the MP3.
- If neither is true, show normal download/progress.
- On `409 track_already_on_device`, update UI to downloaded.

### Saved Songs Screen

Use `/saved-songs` for server-cached songs.

Use your device's own local database/filesystem for truly local offline songs. The backend only knows device-local songs if you sync or confirm them using the device library endpoints.

### Search UI

Because `/search` is SSE:

- Start rendering after `event: start`.
- Append each `event: track`.
- Show non-blocking source notices for `event: warning`.
- Stop loading on `event: done`.

Cached matches are emitted before provider results, so they should appear instantly when available.

### Missing Result Recovery

If `/stream/{result_id}` or `/download/{result_id}` returns `search_result_not_found`:

1. If the track came from `/saved-songs`, call `/saved-songs` again.
2. If it came from search, repeat the search.
3. Match by `result_id`.
4. Retry with the returned `result_id`.

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Install `ffmpeg` and make sure it is available in `PATH`.

Start the API:

```bash
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```
