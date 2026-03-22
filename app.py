import concurrent.futures
import concurrent.futures
import logging
import os
import sys
import threading
from urllib.parse import urlparse
from flask import Flask, jsonify, render_template, request, send_from_directory
from waitress import serve
import downloader
import state


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


# Initialize thread pool executor
executor = concurrent.futures.ThreadPoolExecutor(max_workers=downloader.MAX_WORKERS)

# Start background worker thread
worker_thread = threading.Thread(
    target=downloader.queue_worker_loop,
    args=(downloader.download_queue, executor, downloader.COOKIES_FILE_PATH),
    daemon=True,
)
worker_thread.start()


@app.route("/")
def index():
    """Serves the main HTML page."""
    return render_template("index.html", enable_delete=ENABLE_DELETE, auto_playlist=AUTO_PLAYLIST)


@app.route("/api/get-info")
def get_info():
    """Fetches suggested title and track info for a URL."""
    url = request.args.get("url")
    if not url:
        return jsonify({"success": False, "error": "URL is required."}), 400
    
    info = downloader.get_url_info(url, downloader.COOKIES_FILE_PATH)
    return jsonify(info)


@app.route("/api/create-playlist", methods=["POST"])
def create_playlist():
    """Creates an empty M3U8 file and queues tracks for download."""
    data = request.get_json()
    url = data.get("url")
    name = data.get("name")
    overwrite = bool(data.get("overwrite", False))
    
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

    return jsonify({
        "success": True,
        "playlist_id": sanitized_name,
        "path": playlist_path,
        "track_count": len(entries)
    })


@app.route("/api/download", methods=["GET", "POST"])
def unified_download():
    """
    Unified endpoint to add a URL (YouTube or Spotify) to the queue.
    Supports GET/POST with query param 'url', form data, or JSON body.
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
    job_type = "spotify" if is_spotify else "youtube"

    with state.status_lock:
        state.download_statuses[url] = "queued"

    downloader.download_queue.put({"type": job_type, "url": url, "create_m3u": create_m3u})

    return jsonify({"success": True, "message": f"URL added to {job_type} queue."})


@app.route("/api/status")
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
def logs():
    after = request.args.get("after", default=0, type=int)
    with state.log_lock:
        entries = [entry for entry in state.log_entries if entry["id"] > after]
        latest_id = state.log_entries[-1]["id"] if state.log_entries else after
    return jsonify({"entries": entries, "latest_id": latest_id})


DOWNLOADS_DIR = "downloads"
ENABLE_DELETE = os.environ.get("ENABLE_DELETE", "false").lower() == "true"
AUTO_PLAYLIST = os.environ.get("AUTO_PLAYLIST", "false").lower() == "true"

@app.route("/api/downloaded_files")
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
