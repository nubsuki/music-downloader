# Music Downloader

A web-based music downloader application built with `yt-dlp` and `spotdl`.

## Features

- **Web Interface**: Clean and simple web UI for adding and monitoring downloads.
- **Unified Download**: Supports both **YouTube** and **Spotify** URLs via a single input.
- **M3U Playlist Support**: 
  - Automatically generates `.m3u8` playlists for YouTube and Spotify albums/playlists.
  - Supports **Unicode** (Japanese, Korean, etc.) correctly in Navidrome and VLC.
  - Option to create playlists even for existing songs.
- **Filename Sanitization**: 
  - Automatically transliterates non-ASCII characters to fix compatibility issues with Docker/Windows mounts.
  - Configurable via `RESTRICT_FILENAMES`.
- **Concurrent Downloads**: Multiple simultaneous downloads with configurable worker count.
- **Real-time Status**: Live download progress and status updates.
- **Docker Support**: Containerized deployment with Docker and Docker Compose.
- **Delete Functionality**: Enable or disable delete UI and API endpoints for downloaded files.
- **Download Archive**: Use an archive file to skip already downloaded files based on file existence.

## Installation

### Using Docker

1. Build and run with Docker Compose:
```bash
docker-compose up -d --build
```

### Docker Compose Example

```yaml
services:
  music-downloader:
    container_name: music-downloader
    image: ghcr.io/nubsuki/music-downloader:latest
    ports:
      - "5000:5000"
    environment:
      - ENABLE_DELETE=false # Toggle delete UI and API
      - USE_DOWNLOAD_ARCHIVE=false # "false": checks file existence | "true": uses archive.txt
      - MAX_WORKERS=3
      - RESTRICT_FILENAMES=true # Fixes VLC/Navidrome Unicode issues by forcing ASCII filenames
      - DOWNLOADER_COOKIES_PATH=/app/config/cookies.txt # Optional: for age-restricted content
      - DOWNLOADER_CONFIG_DIR=/app/config
      - SPOTIPY_CLIENT_ID=your_spotify_client_id
      - SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
    volumes:
      - ./downloads:/app/downloads
      - ./config:/app/config
    restart: unless-stopped
```

## Usage

1. Open your browser and navigate to `http://localhost:5000`
2. Paste a **YouTube** or **Spotify** URL in the input field.
3. (Optional) Check the **.m3u** box to generate a playlist file.
4. Click "Add to Queue" to start downloading.

## API

**Endpoint:** `/api/download`
**Methods:** `GET`, `POST`

### GET Example
```bash
curl "http://localhost:5000/api/download?url=https://www.youtube.com/watch?v=VIDEO_ID"
```

### POST Example (JSON)
```bash
curl -X POST http://localhost:5000/api/download \
     -H "Content-Type: application/json" \
     -d '{"url": "https://open.spotify.com/playlist/ID", "create_m3u": true}'
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_WORKERS` | `3` | Number of concurrent downloads. |
| `RESTRICT_FILENAMES` | `false` | If `true`, forces filenames to ASCII (fixes Unicode issues on Docker/Windows). |
| `ENABLE_DELETE` | `false` | Enables delete button in the UI. |
| `USE_DOWNLOAD_ARCHIVE` | `false` | Tracks downloaded IDs to prevent duplicates. |
| `SPOTIPY_CLIENT_ID` | - | Required for Spotify downloads. |
| `SPOTIPY_CLIENT_SECRET` | - | Required for Spotify downloads. |

## Disclaimer

This tool is for educational purposes only. Ensure you have the right to download content and comply with all applicable laws and terms of service.
