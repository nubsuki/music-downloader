import concurrent.futures
import os
import threading
from flask import Flask, jsonify, render_template, request, send_from_directory

import downloader
import state


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
    return render_template("index.html")


@app.route("/api/add_url", methods=["POST"])
def add_url():
    """Adds a URL to the download queue and registers its status."""
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"success": False, "error": "URL is required."}), 400

    with state.status_lock:
        state.download_statuses[url] = "queued"

    downloader.download_queue.put(url)

    return jsonify({"success": True, "message": "URL added to queue."})


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


DOWNLOADS_DIR = "downloads"


@app.route("/api/downloaded_files")
def downloaded_files():
    """Returns a list of files in the downloads directory."""
    if not os.path.exists(DOWNLOADS_DIR):
        os.makedirs(DOWNLOADS_DIR)
        return jsonify([])

    try:
        files = [
            f
            for f in os.listdir(DOWNLOADS_DIR)
            if os.path.isfile(os.path.join(DOWNLOADS_DIR, f))
        ]
        return jsonify(sorted(files, reverse=True))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete_file", methods=["POST"])
def delete_file():
    """Deletes a file from the downloads directory."""
    data = request.get_json()
    filename = data.get("filename")

    if not filename:
        return jsonify({"success": False, "error": "Filename is required."}), 400

    file_path = os.path.join(DOWNLOADS_DIR, filename)
    
    # Security: ensure path is within downloads directory
    if not os.path.normpath(file_path).startswith(os.path.normpath(DOWNLOADS_DIR)):
        return jsonify({"success": False, "error": "Invalid file path."}), 400

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({"success": True, "message": f"Deleted {filename}."})
        else:
            return jsonify({"success": False, "error": "File not found."}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
    print(f"Starting Flask server...")
    print(f"Open http://127.0.0.1:5000 in your browser.")
    print("-" * 50)

    app.run(debug=True, use_reloader=False)

    print("\n[INFO] Shutting down workers...")
    executor.shutdown(wait=True)
    print("[INFO] Web app and download utility shut down.")
