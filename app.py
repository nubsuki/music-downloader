import concurrent.futures
import json
import logging
import os
import re
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_httpauth import HTTPBasicAuth
from waitress import serve
import downloader
import state
from downloader import extract_youtube_playlist_id


logging.getLogger("waitress.queue").setLevel(logging.ERROR)


class StreamCapture:
    def __init__(self, stream):
        self.stream = stream
        self.buffer = ""
        self.lock = threading.Lock()

    def write(self, message):
        if message is None:
            return
        with self.lock:
            self.stream.write(message)
            self.stream.flush()
            self.buffer += message
            self._drain_buffer()

    def _drain_buffer(self):
        while True:
            idx_n = self.buffer.find("\n")
            idx_r = self.buffer.find("\r")
            if idx_n == -1 and idx_r == -1:
                break
            if idx_n == -1:
                idx = idx_r
                sep = "\r"
            elif idx_r == -1:
                idx = idx_n
                sep = "\n"
            else:
                if idx_n < idx_r:
                    idx = idx_n
                    sep = "\n"
                else:
                    idx = idx_r
                    sep = "\r"
            line = self.buffer[:idx]
            self.buffer = self.buffer[idx + 1 :]
            if line or sep == "\n":
                state.add_log_line(line)

    def flush(self):
        with self.lock:
            if self.buffer:
                state.add_log_line(self.buffer)
                self.buffer = ""
            self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()


sys.stdout = StreamCapture(sys.stdout)
sys.stderr = StreamCapture(sys.stderr)


app = Flask(__name__)
auth = HTTPBasicAuth()

# Authentication configuration
AUTH_USERNAME = os.environ.get("AUTH_USERNAME")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD")

@auth.verify_password
def verify_password(username, password):
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        return True
    return username == AUTH_USERNAME and password == AUTH_PASSWORD

PLAYLIST_TRACKER_FILE = os.path.join(downloader.CONFIG_DIR, "tracked_playlists.json")
playlist_tracker_lock = threading.Lock()


def _load_tracked_playlists():
    if not os.path.exists(PLAYLIST_TRACKER_FILE):
        return []
    try:
        with open(PLAYLIST_TRACKER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_tracked_playlists(items):
    os.makedirs(downloader.CONFIG_DIR, exist_ok=True)
    with open(PLAYLIST_TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _get_remote_playlist_snapshot(url):
    info = downloader.get_url_info(url, downloader.COOKIES_FILE_PATH)
    entries = info.get("entries", []) if isinstance(info, dict) else []
    title = (info.get("title") if isinstance(info, dict) else None) or "Playlist"
    ids = set()
    for entry in entries:
        entry_url = (entry.get("url") if isinstance(entry, dict) else "") or ""
        track_id = _extract_track_id(entry_url)
        if track_id:
            ids.add(track_id)
    return title, entries, ids


def _refresh_tracked_playlist(item, force=False):
    title, entries, remote_ids = _get_remote_playlist_snapshot(item.get("url", ""))
    tracked_ids = set(item.get("tracked_ids") or [])
    if not tracked_ids and int(item.get("tracked_track_count", 0)) == 0 and remote_ids:
        tracked_ids = set(remote_ids)
        item["tracked_ids"] = sorted(tracked_ids)
        item["tracked_track_count"] = len(tracked_ids)

    added_ids = remote_ids - tracked_ids
    removed_ids = tracked_ids - remote_ids
    item["name"] = (item.get("name") or title or "Playlist").strip()
    item["source_title"] = title
    if not item.get("youtube_playlist_id"):
        item["youtube_playlist_id"] = extract_youtube_playlist_id(item.get("url", ""))
    item["remote_track_count"] = len(entries)
    item["new_tracks"] = len(added_ids)
    item["removed_tracks"] = len(removed_ids)
    item["change_count"] = len(added_ids) + len(removed_ids)
    item["last_checked_at"] = int(time.time())
    return item


def _is_youtube_playlist_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    is_youtube = host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
    if not is_youtube:
        return False
    if (parsed.path or "").startswith("/playlist"):
        return True
    return "list" in parse_qs(parsed.query)


def _extract_track_id(raw_url):
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    hostname = (parsed.hostname or "").lower()
    if hostname in ("youtu.be", "www.youtu.be"):
        return parsed.path.strip("/")
    qs = parse_qs(parsed.query)
    return qs.get("v", [""])[0]


def _collect_existing_track_ids(target_dir):
    ids = set()
    if not os.path.exists(target_dir):
        return ids
    pattern = r"\[([A-Za-z0-9_-]{11})\]\.mp3$"
    for root, _, files in os.walk(target_dir):
        for filename in files:
            match = re.search(pattern, filename, re.IGNORECASE)
            if match:
                ids.add(match.group(1))
    return ids


def _read_m3u_track_ids(playlist_path):
    ids = set()
    if not os.path.exists(playlist_path):
        return ids
    try:
        with open(playlist_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.search(r"\[([A-Za-z0-9_-]+)\](?:\.[A-Za-z0-9]+)?$", line)
                if match:
                    ids.add(match.group(1))
    except Exception:
        return set()
    return ids


def _sync_tracked_playlist(item, force=False):
    url = (item.get("url") or "").strip()
    if not url:
        return {"success": False, "error": "Playlist URL is required."}
    if not _is_youtube_playlist_url(url):
        return {"success": False, "error": "Only YouTube playlist URLs are supported for tracking."}

    target_dir = os.path.join(DOWNLOADS_DIR, "youtube")
    os.makedirs(target_dir, exist_ok=True)

    info = downloader.get_url_info(url, downloader.COOKIES_FILE_PATH)
    source_title = (info.get("title") if isinstance(info, dict) else None) or "Playlist"
    entries = info.get("entries", []) if isinstance(info, dict) else []
    if not entries:
        return {"success": False, "error": "Could not read playlist entries from YouTube URL."}

    name_for_file = (item.get("name") or source_title or "Playlist").strip()
    playlist_id = item.get("playlist_id") or downloader.sanitize_playlist_name(name_for_file, target_dir, unique=False)
    playlist_id = os.path.basename(playlist_id) or "Playlist"
    playlist_path = os.path.normpath(os.path.join(target_dir, f"{playlist_id}.m3u8"))
    target_dir_norm = os.path.normpath(target_dir)
    if not playlist_path.startswith(target_dir_norm + os.sep):
        return {"success": False, "error": "Invalid playlist path."}

    normalized_entries = []
    for entry in entries:
        entry_url = (entry.get("url") if isinstance(entry, dict) else "") or ""
        if not entry_url:
            continue
        normalized_entries.append({"url": entry_url, "id": _extract_track_id(entry_url)})

    expected_ids = {entry["id"] for entry in normalized_entries if entry["id"]}
    existing_ids = _collect_existing_track_ids(target_dir)
    m3u_ids = _read_m3u_track_ids(playlist_path)

    up_to_date = (
        (not force)
        and os.path.exists(playlist_path)
        and bool(expected_ids)
        and expected_ids.issubset(existing_ids)
        and expected_ids == m3u_ids
    )

    queued_count = 0
    if not up_to_date:
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
        for entry in normalized_entries:
            entry_url = entry["url"]
            with state.status_lock:
                state.download_statuses[entry_url] = "queued"
            downloader.download_queue.put({
                "type": "youtube",
                "url": entry_url,
                "create_m3u": False,
                "playlist_path": playlist_path,
            })
            queued_count += 1

    now = int(time.time())
    item["name"] = name_for_file
    item["source_title"] = source_title
    item["job_type"] = "youtube"
    item["playlist_id"] = playlist_id
    item["path"] = playlist_path
    item["remote_track_count"] = len(normalized_entries)
    item["tracked_ids"] = sorted(expected_ids)
    item["tracked_track_count"] = len(expected_ids) if expected_ids else len(normalized_entries)
    item["new_tracks"] = 0
    item["removed_tracks"] = 0
    item["change_count"] = 0
    item["last_checked_at"] = now
    item["last_updated_at"] = now

    return {
        "success": True,
        "up_to_date": up_to_date,
        "queued_count": queued_count,
        "track_count": len(normalized_entries),
    }


# Initialize thread pool executor
executor = concurrent.futures.ThreadPoolExecutor(max_workers=downloader.MAX_WORKERS)

# Start background worker thread
worker_thread = threading.Thread(
    target=downloader.queue_worker_loop,
    args=(downloader.download_queue, executor, downloader.COOKIES_FILE_PATH),
    daemon=True,
)
worker_thread.start()

# Background thread for auto-refreshing tracked playlists daily
def auto_refresh_thread():
    while True:
        try:
            print("[INFO] Checking playlists for auto-refresh...")
            with playlist_tracker_lock:
                items = _load_tracked_playlists()
                updated_items = []
                for item in items:
                    if item.get("auto_refresh", False):
                        refreshed_item = _refresh_tracked_playlist(dict(item), force=True)
                        # Check if there are changes
                        if refreshed_item.get("new_tracks", 0) > 0 or refreshed_item.get("removed_tracks", 0) > 0:
                            print(f"[INFO] Auto-refreshing playlist: {refreshed_item.get('name', 'Playlist')}")
                            sync_result = _sync_tracked_playlist(refreshed_item, force=True)
                            if sync_result.get("success"):
                                updated_items.append(refreshed_item)
                            else:
                                updated_items.append(refreshed_item)
                        else:
                            updated_items.append(refreshed_item)
                    else:
                        updated_items.append(item)
                _save_tracked_playlists(updated_items)
        except Exception as e:
            logging.error("Error in auto-refresh thread", exc_info=True)
        # Sleep for 1 day
        time.sleep(86400)

# Start the auto-refresh thread
refresh_thread = threading.Thread(target=auto_refresh_thread, daemon=True)
refresh_thread.start()


@app.route("/")
@auth.login_required
def index():
    """Serves the main HTML page."""
    return render_template("index.html", enable_delete=ENABLE_DELETE, auto_playlist=AUTO_PLAYLIST, enable_spotify=ENABLE_SPOTIFY)


@app.route("/api/get-info")
@auth.login_required
def get_info():
    """Fetches suggested title and track info for a URL."""
    url = request.args.get("url")
    if not url:
        return jsonify({"success": False, "error": "URL is required."}), 400
    
    # Check if Spotify is disabled
    if not ENABLE_SPOTIFY:
        parsed = urlparse(url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        is_spotify = (
            parsed.scheme == "spotify" or
            hostname == "spotify.com" or
            hostname.endswith(".spotify.com")
        )
        if is_spotify:
            return jsonify({"success": False, "error": "Spotify functionality is disabled."}), 400
    
    info = downloader.get_url_info(url, downloader.COOKIES_FILE_PATH)
    return jsonify(info)


@app.route("/api/create-playlist", methods=["POST"])
@auth.login_required
def create_playlist():
    """Creates an empty .m3u8 file and queues tracks for download."""
    data = request.get_json()
    url = data.get("url")
    name = data.get("name")
    overwrite = bool(data.get("overwrite", False))
    track_playlist = bool(data.get("track_playlist", False))
    
    if not url or not name:
        return jsonify({"success": False, "error": "URL and name are required."}), 400
    
    # Determine job type and directory
    parsed = urlparse(url)
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    is_spotify = (
        parsed.scheme == "spotify" or
        hostname == "spotify.com" or
        hostname.endswith(".spotify.com")
    )
    
    if is_spotify and not ENABLE_SPOTIFY:
        return jsonify({"success": False, "error": "Spotify functionality is disabled."}), 400
    
    job_type = "spotify" if is_spotify else "youtube"
    
    # Save playlist in the same folder as the songs
    target_dir = os.path.join(DOWNLOADS_DIR, job_type)
    os.makedirs(target_dir, exist_ok=True)
    
    # Use sanitized base name without forcing uniqueness and validate it
    sanitized_name = downloader.sanitize_playlist_name(name, target_dir, unique=False)
    sanitized_name = os.path.basename(sanitized_name)
    if not sanitized_name or sanitized_name in (".", ".."):
        sanitized_name = "Playlist"
    playlist_path = os.path.join(target_dir, f"{sanitized_name}.m3u8")
    playlist_path = os.path.normpath(playlist_path)
    target_dir_norm = os.path.normpath(target_dir)
    if not playlist_path.startswith(target_dir_norm + os.sep):
        return jsonify({"success": False, "error": "Invalid playlist name."}), 400

    # Check for existing playlist by YouTube playlist ID first
    yt_playlist_id = extract_youtube_playlist_id(url) if job_type == "youtube" else None
    existing_yt_playlist = None
    if yt_playlist_id:
        with playlist_tracker_lock:
            items = _load_tracked_playlists()
            existing_yt_playlist = next((x for x in items if x.get("youtube_playlist_id") == yt_playlist_id), None)

    if existing_yt_playlist and not overwrite:
        return jsonify({
            "success": False,
            "error": f"This playlist is already tracked as '{existing_yt_playlist.get('name')}'.",
            "exists": True,
            "exists_as_tracked": True,
            "existing_tracked_playlist": existing_yt_playlist,
            "existing_path": existing_yt_playlist.get("path")
        }), 409

    if os.path.exists(playlist_path) and not overwrite:
        suggested = downloader.sanitize_playlist_name(name, target_dir, unique=True)
        return jsonify({
            "success": False,
            "error": "Playlist already exists.",
            "exists": True,
            "suggested_name": suggested,
            "existing_path": playlist_path
        }), 409
    
    # Create or overwrite M3U8 file
    try:
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
    except Exception as e:
        logging.error("Failed to create playlist file '%s'", playlist_path, exc_info=True)
        return jsonify({"success": False, "error": "Failed to create playlist file."}), 500

    # Fetch track info to queue individual downloads
    info = downloader.get_url_info(url, downloader.COOKIES_FILE_PATH)
    entries = info.get("entries", [])
    if not yt_playlist_id and job_type == "youtube":
        yt_playlist_id = info.get("playlist_id")

    if not entries:
        # Fallback for single video or if info fetch failed
        print(f"[INFO] No entries found in info for {url}, treating as single item.")
        entries = [{"url": url}]
    else:
        print(f"[INFO] Found {len(entries)} entries for playlist {sanitized_name}")

    for entry in entries:
        entry_url = entry.get("url")
        with state.status_lock:
            state.download_statuses[entry_url] = "queued"
        
        downloader.download_queue.put({
            "type": job_type,
            "url": entry_url,
            "create_m3u": False, # manually managing the M3U8
            "playlist_path": playlist_path
        })

    if track_playlist:
        # Track this playlist
        with playlist_tracker_lock:
            items = _load_tracked_playlists()
            target = next((x for x in items if x.get("url") == url or (yt_playlist_id and x.get("youtube_playlist_id") == yt_playlist_id)), None)
            if not target:
                target = {
                    "id": f"pl-{int(time.time() * 1000)}",
                    "url": url,
                    "youtube_playlist_id": yt_playlist_id,
                    "created_at": int(time.time()),
                    "tracked_track_count": 0,
                    "remote_track_count": 0,
                    "new_tracks": 0,
                    "auto_refresh": False,
                }
                items.append(target)
            target["name"] = name
            target["playlist_id"] = sanitized_name
            target["path"] = playlist_path
            if yt_playlist_id:
                target["youtube_playlist_id"] = yt_playlist_id
            # Update tracked playlist with current entries
            normalized_entries = []
            for entry in entries:
                entry_url = entry.get("url")
                if not entry_url:
                    continue
                normalized_entries.append({"url": entry_url, "id": _extract_track_id(entry_url)})
            expected_ids = {entry["id"] for entry in normalized_entries if entry["id"]}
            target["tracked_ids"] = sorted(expected_ids)
            target["tracked_track_count"] = len(expected_ids) if expected_ids else len(normalized_entries)
            target["remote_track_count"] = len(normalized_entries)
            target["new_tracks"] = 0
            target["removed_tracks"] = 0
            target["change_count"] = 0
            target["last_checked_at"] = int(time.time())
            target["last_updated_at"] = int(time.time())
            _save_tracked_playlists(items)

    return jsonify({
        "success": True,
        "playlist_id": sanitized_name,
        "path": playlist_path,
        "track_count": len(entries)
    })


@app.route("/api/tracked_playlists", methods=["GET", "POST"])
@auth.login_required
def tracked_playlists():
    if request.method == "GET":
        force = request.args.get("force", "false").lower() == "true"
        with playlist_tracker_lock:
            items = _load_tracked_playlists()
            if force:
                print(f"[INFO] Force-refreshing {len(items)} tracked playlists...")
                refreshed_items = [_refresh_tracked_playlist(dict(item), force=True) for item in items]
                _save_tracked_playlists(refreshed_items)
                print(f"[INFO] Done refreshing and saved to tracked_playlists.json")
                return jsonify({"playlists": refreshed_items})
            else:
                return jsonify({"playlists": items})
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        requested_name = (data.get("name") or "").strip()
        auto_refresh = bool(data.get("auto_refresh", False))
        playlist_id = (data.get("id") or "").strip()
        
        # If we have a playlist_id, we're just updating auto_refresh flag
        if playlist_id:
            with playlist_tracker_lock:
                items = _load_tracked_playlists()
                target = next((x for x in items if x.get("id") == playlist_id), None)
                if not target:
                    return jsonify({"success": False, "error": "Playlist not found."}), 404
                target["auto_refresh"] = auto_refresh
                _save_tracked_playlists(items)
                return jsonify({"success": True, "playlist": target})
        
        # Otherwise, it's a new playlist to track
        if not url:
            return jsonify({"success": False, "error": "Playlist URL is required."}), 400

        yt_playlist_id = extract_youtube_playlist_id(url)
        if not yt_playlist_id:
            info = downloader.get_url_info(url, downloader.COOKIES_FILE_PATH)
            yt_playlist_id = info.get("playlist_id")

        with playlist_tracker_lock:
            items = _load_tracked_playlists()
            target = next((x for x in items if x.get("url") == url or (yt_playlist_id and x.get("youtube_playlist_id") == yt_playlist_id)), None)
            was_existing = target is not None
            if not target:
                target = {
                    "id": f"pl-{int(time.time() * 1000)}",
                    "url": url,
                    "youtube_playlist_id": yt_playlist_id,
                    "created_at": int(time.time()),
                    "tracked_track_count": 0,
                    "remote_track_count": 0,
                    "new_tracks": 0,
                    "auto_refresh": auto_refresh,
                }
                items.append(target)
            else:
                target["auto_refresh"] = auto_refresh
                if yt_playlist_id and not target.get("youtube_playlist_id"):
                    target["youtube_playlist_id"] = yt_playlist_id

            if requested_name:
                target["name"] = requested_name
                if not target.get("playlist_id"):
                    target_dir = os.path.join(DOWNLOADS_DIR, "youtube")
                    os.makedirs(target_dir, exist_ok=True)
                    target["playlist_id"] = downloader.sanitize_playlist_name(requested_name, target_dir, unique=False)

            sync_result = _sync_tracked_playlist(target, force=False)
            if not sync_result.get("success"):
                return jsonify(sync_result), 400

            _save_tracked_playlists(items)
            return jsonify({
                "success": True,
                "exists": was_existing,
                "playlist": target,
                "sync": sync_result,
                "message": "Playlist is up to date." if sync_result.get("up_to_date") else f"Playlist sync started. Queued {sync_result.get('queued_count', 0)} track(s).",
            })


@app.route("/api/tracked_playlists/ack-update", methods=["POST"])
@auth.login_required
def ack_tracked_playlist_update():
    data = request.get_json(silent=True) or {}
    playlist_id = (data.get("id") or "").strip()
    if not playlist_id:
        return jsonify({"success": False, "error": "Playlist id is required."}), 400

    with playlist_tracker_lock:
        items = _load_tracked_playlists()
        target = next((x for x in items if x.get("id") == playlist_id), None)
        if not target:
            return jsonify({"success": False, "error": "Playlist not found."}), 404

        sync_result = _sync_tracked_playlist(target, force=True)
        if not sync_result.get("success"):
            return jsonify(sync_result), 400

        _save_tracked_playlists(items)

    return jsonify({
        "success": True,
        "playlist": target,
        "sync": sync_result,
        "message": f"Playlist resync started. Queued {sync_result.get('queued_count', 0)} track(s).",
    })


@app.route("/api/tracked_playlists/delete", methods=["POST"])
@auth.login_required
def delete_tracked_playlist():
    if not ENABLE_DELETE:
        return jsonify({"success": False, "error": "Delete functionality is disabled."}), 403

    data = request.get_json(silent=True) or {}
    playlist_id = (data.get("id") or "").strip()
    delete_m3u8 = bool(data.get("delete_m3u8", False))

    if not playlist_id:
        return jsonify({"success": False, "error": "Playlist id is required."}), 400

    with playlist_tracker_lock:
        items = _load_tracked_playlists()
        target_index = next((i for i, x in enumerate(items) if x.get("id") == playlist_id), None)
        if target_index is None:
            return jsonify({"success": False, "error": "Playlist not found."}), 404

        target = items[target_index]
        playlist_path = os.path.normpath(target.get("path") or "")
        youtube_dir = os.path.normpath(os.path.join(DOWNLOADS_DIR, "youtube"))

        removed_m3u8 = False
        if delete_m3u8 and playlist_path and playlist_path.startswith(youtube_dir + os.sep):
            if os.path.exists(playlist_path) and os.path.isfile(playlist_path):
                os.remove(playlist_path)
                removed_m3u8 = True

        removed_playlist = items.pop(target_index)
        _save_tracked_playlists(items)

    return jsonify({
        "success": True,
        "playlist": removed_playlist,
        "removed_m3u8": removed_m3u8,
        "message": "Playlist removed from tracking." if not removed_m3u8 else "Playlist removed from tracking and m3u8 deleted.",
    })


@app.route("/api/download", methods=["GET", "POST"])
@auth.login_required
def unified_download():
    """
    Unified endpoint to add a URL (YouTube or Spotify) to the queue.
    Supports GET/POST with query param "url", form data, or JSON body.
    """
    url = None
    create_m3u = False
    if request.is_json:
        data = request.get_json(silent=True)
        if data:
            url = data.get("url")
            create_m3u = data.get("create_m3u", False)

    if not url:
        url = request.values.get("url")

    if not url:
        return jsonify({"success": False, "error": "URL is required."}), 400

    parsed = urlparse(url)
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    is_spotify = (
        parsed.scheme == "spotify" or
        hostname == "spotify.com" or
        hostname.endswith(".spotify.com")
    )
    
    if is_spotify and not ENABLE_SPOTIFY:
        return jsonify({"success": False, "error": "Spotify functionality is disabled."}), 400
    
    job_type = "spotify" if is_spotify else "youtube"

    with state.status_lock:
        state.download_statuses[url] = "queued"

    downloader.download_queue.put({"type": job_type, "url": url, "create_m3u": create_m3u})

    return jsonify({"success": True, "message": f"URL added to {job_type} queue."})


@app.route("/api/status")
@auth.login_required
def status():
    """Returns a detailed list of all downloads and their statuses."""
    status_report = {
        "downloading": [],
        "queued": [],
        "completed": [],
        "failed": [],
    }
    with state.status_lock:
        for url, status in state.download_statuses.items():
            if status == "downloading":
                status_report["downloading"].append(url)
            elif status == "queued":
                status_report["queued"].append(url)
            elif status == "completed":
                status_report["completed"].append(url)
            elif status.startswith("failed"):
                status_report["failed"].append({"url": url, "error": status})

    return jsonify(status_report)


@app.route("/api/logs")
@auth.login_required
def logs():
    after = request.args.get("after", default=0, type=int)
    with state.log_lock:
        entries = [entry for entry in state.log_entries if entry["id"] > after]
        latest_id = state.log_entries[-1]["id"] if state.log_entries else after
    return jsonify({"entries": entries, "latest_id": latest_id})


DOWNLOADS_DIR = "downloads"
ENABLE_DELETE = os.environ.get("ENABLE_DELETE", "false").lower() == "true"
AUTO_PLAYLIST = os.environ.get("AUTO_PLAYLIST", "false").lower() == "true"
ENABLE_SPOTIFY = os.environ.get("ENABLE_SPOTIFY", "true").lower() == "true"

@app.route("/api/downloaded_files")
@auth.login_required
def downloaded_files():
    """Returns a list of MP3 files in the downloads directory with MP3 count information."""
    if not os.path.exists(DOWNLOADS_DIR):
        os.makedirs(DOWNLOADS_DIR)
        return jsonify({"files": [], "mp3_count": 0})

    try:
        # Get only MP3 files
        mp3_files = []
        for root, _, files in os.walk(DOWNLOADS_DIR):
            for f in files:
                if f.lower().endswith(".mp3"):
                    rel_path = os.path.relpath(os.path.join(root, f), DOWNLOADS_DIR)
                    mp3_files.append(rel_path.replace(os.sep, "/"))

        # Count MP3 files
        mp3_count = len(mp3_files)

        return jsonify({
            "files": sorted(mp3_files, reverse=True),
            "mp3_count": mp3_count
        })
    except Exception:
        return jsonify({"error": "An error occurred while retrieving the file list."}), 500


@app.route("/api/delete_file", methods=["POST"])
@auth.login_required
def delete_file():
    """Deletes a file from the downloads directory."""
    if not ENABLE_DELETE:
        return jsonify({"success": False, "error": "Delete functionality is disabled."}), 403

    data = request.get_json()
    filename = data.get("filename")

    if not filename:
        return jsonify({"success": False, "error": "Filename is required."}), 400

    file_path = os.path.normpath(os.path.join(DOWNLOADS_DIR, filename))
    downloads_dir_normalized = os.path.normpath(DOWNLOADS_DIR)
    
    if not file_path.startswith(downloads_dir_normalized):
        return jsonify({"success": False, "error": "Invalid file path."}), 400

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({"success": True, "message": f"Deleted {filename}."})
        else:
            return jsonify({"success": False, "error": "File not found."}), 404
    except Exception:
        return jsonify({"success": False, "error": "An error occurred while deleting the file."}), 500


@app.route("/downloads/<path:filename>")
@auth.login_required
def serve_downloaded_file(filename):
    """Serves a file from the downloads directory."""
    return send_from_directory(DOWNLOADS_DIR, filename)


if __name__ == "__main__":
    # Get cookies path from environment
    cookies_path = os.environ.get("DOWNLOADER_COOKIES_PATH")
    if not cookies_path:
        print(
            "\nTo download age-restricted or private videos, you can set the DOWNLOADER_COOKIES_PATH environment variable."
        )
        print("Continuing without authentication.")
    elif not os.path.exists(cookies_path):
        print(
            f"[WARNING] Cookies file not found at: {cookies_path}. Continuing without authentication."
        )
        cookies_path = None
    else:
        print(f"[INFO] Using cookies file: {cookies_path}")

    downloader.COOKIES_FILE_PATH = cookies_path

    print("-" * 50)
    print(f"Starting server")
    
    # Check for Deno
    try:
        import subprocess
        deno_version = subprocess.check_output(["deno", "--version"], text=True).splitlines()[0]
        print(f"[INFO] Deno is active ({deno_version})")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[INFO] Deno is not found in PATH.")

    print(f"Open http://127.0.0.1:5000 in your browser.")
    print("-" * 50)

    serve(app, host="0.0.0.0", port=5000)

    print("\n[INFO] Shutting down workers...")
    executor.shutdown(wait=True)
    print("[INFO] Web app and download utility shut down.")
