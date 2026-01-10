import os
import subprocess
import sys
from urllib.parse import urlparse, parse_qs
import threading
import queue
import concurrent.futures
import state
from dotenv import load_dotenv
import tempfile
import shutil

load_dotenv()

try:
    script_path = os.path.abspath(__file__)
except NameError:
    script_path = os.path.abspath(sys.argv[0])

script_dir = os.path.dirname(script_path)

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

# Config file path
CONFIG_DIR = os.environ.get("DOWNLOADER_CONFIG_DIR") or os.path.join(script_dir, "config")
USE_DOWNLOAD_ARCHIVE = str(os.environ.get("USE_DOWNLOAD_ARCHIVE", "false")).lower() in ("1", "true", "yes", "on")

# Validate cookies path
if COOKIES_FILE_PATH and not os.path.exists(COOKIES_FILE_PATH):
    print(f"[WARNING] Cookies file not found at: {COOKIES_FILE_PATH}. Continuing without authentication.")
    COOKIES_FILE_PATH = None
elif COOKIES_FILE_PATH:
    print(f"[INFO] Using cookies file: {COOKIES_FILE_PATH}")

def download_youtube_url(
    url: str, cookies_file_path: str, output_path: str = "downloads", create_m3u: bool = False
):
    """
    Downloads a video from a given YouTube URL using yt-dlp,
    specifying a local path for FFmpeg, and converts it to MP3 with album art.
    This function is run by a worker thread and updates the central state.
    """
    with state.status_lock:
        state.download_statuses[url] = "downloading"

    print(f"\n[WORKER] Starting download for: {url}")

    parsed = urlparse(url)
    if not parsed.scheme:
        err_msg = f"Invalid URL format for {url}. Skipping."
        print(f"[ERROR] {err_msg}")
        with state.status_lock:
            state.download_statuses[url] = f"failed: {err_msg}"
        return

    qs = parse_qs(parsed.query)
    is_playlist = parsed.path.startswith("/playlist") or ("list" in qs and not qs.get("v"))
    if is_playlist:
        print("[WORKER] Detected playlist URL.")
        playlist_folder_template = "%(playlist_title)s - by %(playlist_uploader)s"
        # Use index in filename for playlists to ensure correct order in m3u
        file_template = "%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"
    else:
        print("[WORKER] Detected single video URL.")
        playlist_folder_template = "singles"
        file_template = "%(title)s [%(id)s].%(ext)s"

    # Separate folder for YouTube downloads
    youtube_output_path = os.path.join(output_path, "youtube")
    os.makedirs(youtube_output_path, exist_ok=True)

    command = [
        "yt-dlp",
    ]

    # Determine playlist folder name ahead of time if we need to generate an m3u
    playlist_folder_name = None
    if is_playlist and create_m3u:
        try:
            # Run yt-dlp to get the formatted folder name without downloading
            name_cmd = [
                "yt-dlp",
                "--get-filename",
                "-o", playlist_folder_template,
                "--playlist-items", "1",
                "--skip-download",
            ]
            if cookies_file_path:
                name_cmd.extend(["--cookies", cookies_file_path])
            name_cmd.append(url)
            
            res = subprocess.run(name_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if res.returncode == 0:
                playlist_folder_name = res.stdout.strip().split('\n')[0]
        except Exception as e:
            print(f"[WARNING] Could not determine playlist folder name for m3u: {e}")

    if cookies_file_path:
        command.extend(["--cookies", cookies_file_path])

    command.extend([
        "-x",
        "--audio-format", "mp3",
        "--embed-metadata",
        "--embed-thumbnail",
        "--audio-quality", "0",
        "--no-overwrites",
    ])

    if USE_DOWNLOAD_ARCHIVE:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        archive_path = os.path.join(CONFIG_DIR, ".archive.txt")
        command.extend(["--download-archive", archive_path])

    command.extend([
        "--ignore-errors",
        "--yes-playlist" if is_playlist else "--no-playlist",
        "-o", os.path.join(youtube_output_path, playlist_folder_template, file_template),
        url,
    ])

    try:
        subprocess.run(
            command, check=True, capture_output=False, text=True, encoding='utf-8', errors='replace'
        )
        print(f"\n[SUCCESS] Finished download for: {url}")
        with state.status_lock:
            state.download_statuses[url] = "completed"
            
        # Generate m3u playlist if requested
        if is_playlist and create_m3u and playlist_folder_name:
            try:
                target_dir = os.path.join(youtube_output_path, playlist_folder_name)
                if os.path.isdir(target_dir):
                    mp3_files = sorted([f for f in os.listdir(target_dir) if f.lower().endswith('.mp3')])
                    if len(mp3_files) > 0:
                        m3u_path = os.path.join(target_dir, f"{playlist_folder_name}.m3u")
                        with open(m3u_path, 'w', encoding='utf-8') as f:
                            f.write("#EXTM3U\n")
                            for mp3 in mp3_files:
                                f.write(f"{mp3}\n")
                        print(f"[INFO] Created m3u playlist: {m3u_path}")
            except Exception as e:
                print(f"[ERROR] Failed to create m3u playlist: {e}")

    except subprocess.CalledProcessError as e:
        err_msg = f"Download failed (yt-dlp exited with code {e.returncode}). Check console for details."
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


def download_spotify_url(url: str, output_path: str = "downloads", create_m3u: bool = False):
    """
    Downloads a song/playlist from a given Spotify URL using spotdl.
    """
    with state.status_lock:
        state.download_statuses[url] = "downloading"

    print(f"\n[WORKER] Starting Spotify download for: {url}")

    # Separate folder for Spotify downloads
    # Use absolute path to ensure spotdl finds it even if we change CWD
    spotify_output_path = os.path.abspath(os.path.join(output_path, "spotify"))
    os.makedirs(spotify_output_path, exist_ok=True)

    is_single = "/track/" in url or ":track:" in url

    # Determine output template based on URL type
    if is_single:
        # Single track -> spotify/singles/Artist - Title
        output_template = os.path.join("singles", "{title}")
    else:
        # Playlist, Album, or Artist -> spotify/PlaylistName/Artist - Title
        output_template = os.path.join("{list-name}", "{artist} - {title}")
        

    command = [
        "spotdl",
        "download",
        url,
        "--output",
        os.path.join(spotify_output_path, output_template)
    ]

    m3u_temp_dir = None
    # Add m3u generation if requested and it's not a single track
    if create_m3u and not is_single:
        m3u_temp_dir = tempfile.mkdtemp()
        # Use a static name to avoid template errors with spotdl (e.g. when downloading albums)
        command.extend(["--m3u", "_playlist.m3u8"])

    if USE_DOWNLOAD_ARCHIVE:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        archive_path = os.path.join(CONFIG_DIR, ".archive.txt")
        command.extend(["--archive", archive_path])

    # Add Spotify credentials if available to avoid rate limits
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    if client_id and client_secret:
        command.extend(["--client-id", client_id, "--client-secret", client_secret])

    try:
        subprocess.run(
            command, 
            cwd=m3u_temp_dir if m3u_temp_dir else None,
            check=True, 
            capture_output=False, 
            text=True, 
            encoding='utf-8', 
            errors='replace'
        )
        print(f"\n[SUCCESS] Finished Spotify download for: {url}")
        with state.status_lock:
            state.download_statuses[url] = "completed"
            
        # Post-process m3u: Move it from temp dir to the actual album folder
        if m3u_temp_dir:
            try:
                m3u_files = [f for f in os.listdir(m3u_temp_dir) if f.endswith('.m3u8')]
                if m3u_files:
                    m3u_filename = m3u_files[0]
                    m3u_src_path = os.path.join(m3u_temp_dir, m3u_filename)
                    
                    # Read m3u to find the first song filename to locate the folder
                    with open(m3u_src_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    
                    song_lines = [l.strip() for l in lines if l.strip() and not l.startswith('#')]
                    
                    if song_lines and len(song_lines) > 1:
                        # Find where this song is in the spotify output directory
                        first_song_name = os.path.basename(song_lines[0])
                        found_dir = None
                        for root, dirs, files in os.walk(spotify_output_path):
                            if first_song_name in files:
                                found_dir = root
                                break
                        
                        if found_dir:
                            # Use the folder name (which contains Album/Playlist title) for the m3u filename
                            folder_name = os.path.basename(found_dir)
                            final_m3u_name = f"{folder_name}.m3u8"

                            # Rewrite m3u with simple filenames (relative paths) and save to dest
                            dest_path = os.path.join(found_dir, final_m3u_name)
                            with open(dest_path, 'w', encoding='utf-8') as f:
                                for line in lines:
                                    if line.strip() and not line.startswith('#'):
                                        f.write(f"{os.path.basename(line.strip())}\n")
                                    else:
                                        f.write(line)
                            print(f"[INFO] Moved and updated m3u playlist to: {dest_path}")
            except Exception as e:
                print(f"[WARNING] Failed to process m3u file: {e}")
            finally:
                if m3u_temp_dir and os.path.exists(m3u_temp_dir):
                    shutil.rmtree(m3u_temp_dir)

    except subprocess.CalledProcessError as e:
        err_msg = f"Download failed (spotdl exited with code {e.returncode}). Check console for details."
        print(f"\n[ERROR] {err_msg}")
        with state.status_lock:
            state.download_statuses[url] = f"failed: {err_msg}"
    except FileNotFoundError:
        err_msg = "'spotdl' command not found. Please install it (pip install spotdl)."
        print(f"\n[ERROR] {err_msg}")
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
            item = q.get(timeout=1)
            
            if isinstance(item, dict):
                url = item.get("url")
                job_type = item.get("type", "youtube")
                create_m3u = item.get("create_m3u", False)
            else:
                url = item
                job_type = "youtube"
                create_m3u = False

            if job_type == "spotify":
                future = executor.submit(download_spotify_url, url, "downloads", create_m3u)
            else:
                future = executor.submit(download_youtube_url, url, cookies_file_path, "downloads", create_m3u)
                
            future.add_done_callback(lambda _: q.task_done())

        except queue.Empty:
            pass
        except Exception as e:
            print(f"[QUEUE ERROR] An error occurred in the worker loop: {e}")
