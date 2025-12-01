# Music Downloader

A web-based music downloader application built with yt-dlp, YouTube integration, and automatic audio conversion using FFmpeg.

## Features

- **Web Interface**: Clean and Simple web UI for adding and monitoring downloads
- **Concurrent Downloads**: Multiple simultaneous downloads with configurable worker count
- **YouTube Support**: Download audio from YouTube videos
- **FFmpeg Integration**: Automatic audio conversion to MP3 format
- **Real-time Status**: Live download progress and status updates
- **Docker Support**: Containerized deployment with Docker and Docker Compose
- **Cross-platform**: Works on Windows, Linux, and macOS


## Installation

### Using Docker

1. Build and run with Docker Compose:
```bash
docker-compose up -d
```
Example:
```bash
services:
  app:
    container_name: music-downloader
    image: ghcr.io/nubsuki/music-downloader:latest
    ports:
      - "5000:5000"
    environment:
      MAX_WORKERS: "3"
      DOWNLOADER_COOKIES_PATH: "/app/cookies.txt"
    volumes:
      - mnt/drive1/downloads:/app/downloads
      - mnt/drive1/cookies.txt:/app/cookies.txt
    restart: unless-stopped

```

## Usage

1. Open your browser and navigate to `http://localhost:5000`
2. Paste YouTube URL in the input field
3. Click "Add to Queue" to start downloads

## Disclaimer

This tool is for educational purposes only. Ensure you have the right to download content and comply with all applicable laws and terms of service.
