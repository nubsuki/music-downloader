import os
import subprocess
import sys
from urllib.parse import urlparse
import threading
import queue
import concurrent.futures
import state
from dotenv import load_dotenv

load_dotenv()

# Locate local ffmpeg folder
try:
    script_path = os.path.abspath(__file__)
except NameError:
    script_path = os.path.abspath(sys.argv[0])

script_dir = os.path.dirname(script_path)

# Local ffmpeg binary directory
local_ffmpeg_bin_dir = os.path.join(script_dir, "ffmpeg", "bin")

# Global queue and worker configuration
download_queue = queue.Queue()

# Max concurrent downloads (from environment variable)
try:
    MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "3"))
    if MAX_WORKERS < 1:
        print(f"[WARNING] MAX_WORKERS must be at least 1. Using default value of 3.")
        MAX_WORKERS = 3
except (ValueError, TypeError):
    print(f"[WARNING] Invalid MAX_WORKERS value. Using default value of 3.")
    MAX_WORKERS = 3

if MAX_WORKERS != 3:
    print(f"[INFO] Using {MAX_WORKERS} concurrent download workers.")

# Cookies file path
COOKIES_FILE_PATH = os.environ.get("DOWNLOADER_COOKIES_PATH") or None

# Validate cookies path
if COOKIES_FILE_PATH and not os.path.exists(COOKIES_FILE_PATH):
    print(f"[WARNING] Cookies file not found at: {COOKIES_FILE_PATH}. Continuing without authentication.")
    COOKIES_FILE_PATH = None
elif COOKIES_FILE_PATH:
    print(f"[INFO] Using cookies file: {COOKIES_FILE_PATH}")

def download_youtube_url(
    url: str, cookies_file_path: str, output_path: str = "downloads"
):
    """
    Downloads a video from a given YouTube URL using yt-dlp,
    specifying a local path for FFmpeg, and converts it to MP3 with album art.
    This function is run by a worker thread and updates the central state.
    """
    with state.status_lock:
        state.download_statuses[url] = "downloading"

    print(f"\n[WORKER] Starting download for: {url}")

    if not urlparse(url).scheme:
        err_msg = f"Invalid URL format for {url}. Skipping."
        print(f"[ERROR] {err_msg}")
        with state.status_lock:
            state.download_statuses[url] = f"failed: {err_msg}"
        return

    os.makedirs(output_path, exist_ok=True)

    command = [
        "yt-dlp",
        f"--ffmpeg-location={local_ffmpeg_bin_dir}",
    ]

    if cookies_file_path:
        command.extend(["--cookies", cookies_file_path])

    command.extend([
        "-x",
        "--audio-format", "mp3",
        "--embed-metadata",
        "--embed-thumbnail",
        "--audio-quality", "0",
        "-o", os.path.join(output_path, "%(title)s.%(ext)s"),
        url,
    ])

    try:
        subprocess.run(
            command, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        print(f"\n[SUCCESS] Finished download for: {url}")
        with state.status_lock:
            state.download_statuses[url] = "completed"

    except subprocess.CalledError as e:
        err_msg = f"Download failed (yt-dlp exited with code {e.returncode}). Stderr: {e.stderr}"
        print(f"\n[ERROR] {err_msg}")
        with state.status_lock:
            state.download_statuses[url] = f"failed: {err_msg}"
    except FileNotFoundError:
        err_msg = "'yt-dlp' command not found."
        print(f"\n[ERROR] {err_msg} Cannot process {url}.")
        with state.status_lock:
            state.download_statuses[url] = f"failed: {err_msg}"
    except Exception as e:
        err_msg = f"An unexpected error occurred: {e}"
        print(f"\n[ERROR] {err_msg} while downloading {url}")
        with state.status_lock:
            state.download_statuses[url] = f"failed: {err_msg}"


def queue_worker_loop(
    q: queue.Queue, executor: concurrent.futures.ThreadPoolExecutor, cookies_file_path: str
):
    """Continuously monitors the queue and submits download jobs to the thread pool."""
    print(f"[QUEUE] Worker loop started with {MAX_WORKERS} max concurrent threads.")
    while True:
        try:
            url = q.get(timeout=1)
            executor.submit(download_youtube_url, url, cookies_file_path)
            q.task_done()

        except queue.Empty:
            pass
        except Exception as e:
            print(f"[QUEUE ERROR] An error occurred in the worker loop: {e}")