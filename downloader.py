import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse, parse_qs
import queue
import concurrent.futures
import state
from dotenv import load_dotenv
import tempfile
import shutil
import unicodedata

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

RESTRICT_FILENAMES = str(os.environ.get("RESTRICT_FILENAMES", "false")).lower() in ("1", "true", "yes", "on")

# Validate cookies path
if COOKIES_FILE_PATH and not os.path.exists(COOKIES_FILE_PATH):
    print(f"[WARNING] Cookies file not found at: {COOKIES_FILE_PATH}. Continuing without authentication.")
    COOKIES_FILE_PATH = None
elif COOKIES_FILE_PATH:
    print(f"[INFO] Using cookies file: {COOKIES_FILE_PATH}")


def _build_youtube_id_index(youtube_output_path: str) -> dict:
    id_to_path = {}
    for root, _, files in os.walk(youtube_output_path):
        for fn in files:
            if not fn.lower().endswith(".mp3"):
                continue
            m = re.search(r"\[([A-Za-z0-9_-]{11})\]\.mp3$", fn)
            if not m:
                continue
            id_to_path[m.group(1)] = os.path.join(root, fn)
    return id_to_path


def _yt_dlp_get_expected_playlist_files(
    url: str,
    cookies_file_path: str,
    youtube_output_path: str,
    file_template: str,
) -> list:
    cmd = [
        "yt-dlp",
        "--get-filename",
        "--skip-download",
        "--ignore-errors",
        "--yes-playlist",
        "-o",
        os.path.join(youtube_output_path, file_template),
    ]
    
    if RESTRICT_FILENAMES:
        cmd.append("--restrict-filenames")
        
    if cookies_file_path:
        cmd.extend(["--cookies", cookies_file_path])
    cmd.append(url)

    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0 and not res.stdout.strip():
        return []

    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def create_youtube_playlist_m3u(
    url: str,
    cookies_file_path: str,
    youtube_output_path: str,
    playlist_name: str,
    file_template: str,
) -> None:
    target_dir = youtube_output_path

    expected_paths = _yt_dlp_get_expected_playlist_files(
        url, cookies_file_path, youtube_output_path, file_template
    )

    id_index = _build_youtube_id_index(youtube_output_path)

    entries = []
    for p in expected_paths:
        base = os.path.basename(p)
        # Extract the video ID regardless of extension
        m = re.search(r"\[([A-Za-z0-9_-]{11})\]", base)
        vid = m.group(1) if m else None
        # Ensure fallback basename uses .mp3
        mp3_base = os.path.splitext(base)[0] + ".mp3"
        entries.append((vid, mp3_base))

    if not entries and os.path.isdir(target_dir):
        mp3_files = sorted([f for f in os.listdir(target_dir) if f.lower().endswith(".mp3")])
        entries = [(None, f) for f in mp3_files]

    m3u_path = os.path.join(target_dir, f"{playlist_name}.m3u8")
    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for vid, expected_basename in entries:
            actual_path = id_index.get(vid) if vid else None
            if actual_path and os.path.exists(actual_path):
                rel = os.path.relpath(actual_path, target_dir).replace("\\", "/")
                f.write(f"{unicodedata.normalize('NFC', rel)}\n")
            else:
                f.write(f"{unicodedata.normalize('NFC', expected_basename)}\n")

    print(f"[INFO] Created m3u playlist: {m3u_path}")


def _sanitize_filename_component(name: str) -> str:
    if name is None:
        return ""
    s = str(name)
    
    if RESTRICT_FILENAMES:
        s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
        s = re.sub(r"[^a-zA-Z0-9\.\-]", "_", s)
        s = re.sub(r"_+", "_", s)

    else:
        s = re.sub(r"[<>:\"/\\|?*]", "_", s)
        s = re.sub(r"\s+", " ", s).strip()
        s = s.rstrip(". ")
    
    return s


def _spotdl_save_query(
    url: str,
    save_file_path: str,
    client_id: str,
    client_secret: str,
    cwd: str,
) -> None:
    cmd = ["spotdl", "save", url, "--save-file", save_file_path]
    if client_id and client_secret:
        cmd.extend(["--client-id", client_id, "--client-secret", client_secret])
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=False, text=True, encoding="utf-8", errors="replace")


def _load_spotdl_save_file(save_file_path: str):
    with open(save_file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _spotdl_extract_songs(save_data) -> list:
    if isinstance(save_data, list):
        return save_data
    if isinstance(save_data, dict):
        for k in ("songs", "items", "tracks", "urls"):
            v = save_data.get(k)
            if isinstance(v, list):
                return v
    return []


def _spotdl_song_artist_title(song: dict) -> tuple:
    title = song.get("title") or song.get("name")
    artist = song.get("artist")
    if not artist:
        artists = song.get("artists")
        if isinstance(artists, list) and artists:
            a0 = artists[0]
            if isinstance(a0, dict):
                artist = a0.get("name")
            else:
                artist = str(a0)
        elif isinstance(artists, str):
            artist = artists.split(",")[0].strip()
    return artist, title


def _spotdl_song_track_id(song: dict) -> str:
    tid = (
        song.get("track_id")
        or song.get("track-id")
        or song.get("trackId")
        or song.get("id")
        or song.get("spotify_id")
        or song.get("song_id")
    )
    if isinstance(tid, str) and tid:
        return tid
    return ""


def create_spotify_m3u_from_save(save_file_path: str, spotify_output_path: str) -> None:
    if not save_file_path or not os.path.exists(save_file_path):
        return

    save_data = _load_spotdl_save_file(save_file_path)
    songs = _spotdl_extract_songs(save_data)

    default_list_name = None
    if isinstance(save_data, dict):
        default_list_name = save_data.get("name") or save_data.get("list_name") or save_data.get("list-name")

    groups = {}
    for s in songs:
        if not isinstance(s, dict):
            continue
        list_name = s.get("list_name") or s.get("list-name") or s.get("listName") or default_list_name
        list_name = _sanitize_filename_component(list_name) or "Spotify Playlist"

        track_id = _spotdl_song_track_id(s)
        if not track_id:
            continue

        # Group by list; keep track ID
        groups.setdefault(list_name, []).append((track_id, s))

    if not groups:
        print("[WARNING] No songs found in Spotify save metadata. Skipping M3U creation.")
        return

    # Index existing files by track ID
    track_id_map = {}
    valid_exts = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav"}
    
    if os.path.exists(spotify_output_path):
        for fname in os.listdir(spotify_output_path):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in valid_exts:
                continue
            # Detect ID: "... [ID].ext"
            m = re.search(r"\[([A-Za-z0-9_-]+)\]" + re.escape(ext) + "$", fname)
            if m:
                track_id_map[m.group(1)] = fname

    for list_name, entries in groups.items():
        print(f"[INFO] Building Spotify M3U for list '{list_name}' with {len(entries)} entries...")
        m3u_path = os.path.join(spotify_output_path, f"{list_name}.m3u8")
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for track_id, song_data in entries:
                    # Use existing file if found
                    if track_id in track_id_map:
                        f.write(f"{unicodedata.normalize('NFC', track_id_map[track_id])}\n")
                    else:
                        # strip artist as requested
                        _, title = _spotdl_song_artist_title(song_data)
                        if title:
                            base = _sanitize_filename_component(f"{title} [{track_id}]")
                            f.write(f"{unicodedata.normalize('NFC', base)}.mp3\n")

            print(f"[INFO] Created m3u playlist: {m3u_path}")
        except Exception as e:
            print(f"[ERROR] Failed to write m3u file {m3u_path}: {e}")


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
    
    # Unified file template (no numbering)
    file_template = "%(title)s [%(id)s].%(ext)s"
    playlist_name_template = None

    if is_playlist:
        print("[WORKER] Detected playlist URL.")
        playlist_name_template = "%(playlist_title)s - by %(playlist_uploader)s"
    else:
        print("[WORKER] Detected single video URL.")

    # YouTube target dir
    youtube_output_path = os.path.join(output_path, "youtube")
    os.makedirs(youtube_output_path, exist_ok=True)

    command = [
        "yt-dlp",
    ]

    # Fetch playlist name for M3U
    playlist_name = None
    if is_playlist and create_m3u and playlist_name_template:
        try:
            # Ask yt-dlp for a formatted name (no download)
            name_cmd = [
                "yt-dlp",
                "--get-filename",
                "-o", playlist_name_template,
                "--playlist-items", "1",
                "--skip-download",
            ]
            if cookies_file_path:
                name_cmd.extend(["--cookies", cookies_file_path])
            name_cmd.append(url)
            
            res = subprocess.run(name_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if res.returncode == 0:
                playlist_name = res.stdout.strip().split('\n')[0]
        except Exception as e:
            print(f"[WARNING] Could not determine playlist name for m3u: {e}")

    if cookies_file_path:
        command.extend(["--cookies", cookies_file_path])

    if RESTRICT_FILENAMES:
        command.append("--restrict-filenames")

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
        "-o",
        os.path.join(youtube_output_path, file_template),
        url,
    ])

    try:
        subprocess.run(
            command, check=True, capture_output=False, text=True, encoding='utf-8', errors='replace'
        )
        print(f"\n[SUCCESS] Finished download for: {url}")
        with state.status_lock:
            state.download_statuses[url] = "completed"
            
        if is_playlist and create_m3u and playlist_name:
            try:
                create_youtube_playlist_m3u(
                    url,
                    cookies_file_path,
                    youtube_output_path,
                    playlist_name,
                    file_template,
                )
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

    spotify_output_path = os.path.abspath(os.path.join(output_path, "spotify"))
    os.makedirs(spotify_output_path, exist_ok=True)

    is_single = "/track/" in url or ":track:" in url

    # Template without artist and numbering
    output_template = "{title} [{track-id}].{output-ext}"

    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

    # Save metadata for M3U
    save_temp_dir = None
    save_file_path = None
    
    if create_m3u and not is_single:
        try:
            # Use a temp dir inside the output path to avoid permission issues in Docker /tmp
            save_temp_dir = os.path.join(spotify_output_path, ".spotdl_temp")
            os.makedirs(save_temp_dir, exist_ok=True)
            save_file_path = os.path.join(save_temp_dir, "query.spotdl")
            
            print(f"[INFO] Saving Spotify metadata to {save_file_path}...")
            # Build M3U from saved metadata
            _spotdl_save_query(url, save_file_path, client_id, client_secret, save_temp_dir)
        except Exception as e:
            print(f"[WARNING] Failed to save Spotify metadata: {e}")
            # If save fails, we proceed to download but skip m3u creation
            save_file_path = None

    # Download to target dir
    command = [
        "spotdl",
        "download",
        url,
        "--output",
        output_template,
        "--format", "mp3",  # Explicitly enforce mp3
    ]

    if RESTRICT_FILENAMES:
        command.append("--restrict")

    if USE_DOWNLOAD_ARCHIVE:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        archive_path = os.path.join(CONFIG_DIR, ".archive.txt")
        command.extend(["--archive", archive_path])

    if client_id and client_secret:
        command.extend(["--client-id", client_id, "--client-secret", client_secret])

    try:
        subprocess.run(
            command,
            cwd=spotify_output_path,  # Run in the output directory
            check=True,
            capture_output=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        print(f"\n[SUCCESS] Finished Spotify download for: {url}")
        with state.status_lock:
            state.download_statuses[url] = "completed"

        # Create M3U
        if create_m3u and not is_single and save_file_path:
            try:
                print(f"[INFO] Attempting to create M3U from save file: {save_file_path}")
                create_spotify_m3u_from_save(save_file_path, spotify_output_path)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[WARNING] Failed to create Spotify m3u playlist: {e}")

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
    finally:
        if save_temp_dir and os.path.exists(save_temp_dir):
            shutil.rmtree(save_temp_dir)


def queue_worker_loop(
    q: queue.Queue, executor: concurrent.futures.ThreadPoolExecutor, cookies_file_path: str
):
    """Monitor the queue and submit jobs to the thread pool."""
    print(f"[QUEUE] Worker loop started with {MAX_WORKERS} max concurrent threads.")
    while True:
        try:
            item = q.get(timeout=1)
            
            if isinstance(item, dict):
                url = item.get("url")
                job_type = item.get("type", "youtube")
                create_m3u = item.get("create_m3u", None)
            else:
                url = item
                job_type = "youtube"
                create_m3u = None

            # Default M3U for Spotify unless explicitly disabled
            if create_m3u is None:
                create_m3u = (job_type == "spotify")

            if job_type == "spotify":
                future = executor.submit(download_spotify_url, url, "downloads", create_m3u)
            else:
                future = executor.submit(download_youtube_url, url, cookies_file_path, "downloads", create_m3u)
                
            future.add_done_callback(lambda _: q.task_done())

        except queue.Empty:
            pass
        except Exception as e:
            print(f"[QUEUE ERROR] An error occurred in the worker loop: {e}")
