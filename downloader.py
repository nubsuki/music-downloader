import os
import subprocess
import sys
from urllib.parse import urlparse
import threading
import queue
import concurrent.futures


# locate the local ffmpeg folder.
try:
    script_path = os.path.abspath(__file__)
except NameError:
    script_path = os.path.abspath(sys.argv[0])

script_dir = os.path.dirname(script_path)

# Path to the local ffmpeg binary directory
local_ffmpeg_bin_dir = os.path.join(script_dir, "ffmpeg", "bin")

# Global Queue and Worker Configuration
download_queue = queue.Queue()
MAX_WORKERS = 3 # Maximum number of concurrent downloads

# Global variable to store the cookies file path
COOKIES_FILE_PATH = None 

print(f"Script Directory: {script_dir}")
print(f"Expected FFmpeg Bin Path: {local_ffmpeg_bin_dir}")

def download_youtube_url(url: str, cookies_file_path: str, output_path: str = "downloads"):
    """
    Downloads a video from a given YouTube URL using yt-dlp,
    specifying a local path for FFmpeg, and converts it to MP3 with album art.
    This function is run by a worker thread.
    """
    print(f"\n[WORKER] Starting download for: {url}")

    if not urlparse(url).scheme:
        print(f"[ERROR] Invalid URL format for {url}. Skipping.")
        return

    # Create the output directory if it doesn't exist
    os.makedirs(output_path, exist_ok=True)

    # --- yt-dlp Command Construction for MP3 ---

    command = [
        "yt-dlp",
        # Point yt-dlp to the local ffmpeg binaries
        f"--ffmpeg-location={local_ffmpeg_bin_dir}",
    ]
    
    # Authentication: Add cookies flag if a path is provided
    if cookies_file_path:
        command.extend(["--cookies", cookies_file_path])
        
    command.extend([
        # Audio Extraction Flags
        "-x",                           # Extract audio stream
        "--audio-format", "mp3",        # Convert extracted audio to MP3
        "--embed-metadata",             # Embed song metadata
        "--embed-thumbnail",            # Embed video thumbnail as album art
        
        # Set audio quality: "0" = best available quality for the format
        "--audio-quality", "0",

        # Output template: 'downloads/Video Title.mp3'
        "-o", os.path.join(output_path, "%(title)s.%(ext)s"),
        # The target URL
        url
    ])

    try:
        # Run the command and stream output directly to console
        subprocess.run(
            command,
            check=True,  # Raise error on non-zero exit code
            capture_output=False,
            text=True
        )
        print(f"\n[SUCCESS] Finished download for: {url}")

    except subprocess.CalledError as e:
        print(f"\n[ERROR] Download failed for {url} (yt-dlp exited with code {e.returncode}).")
    except FileNotFoundError:
        print(f"\n[ERROR] 'yt-dlp' command not found. Cannot process {url}.")
    except Exception as e:
        print(f"\n[ERROR] An unexpected error occurred while downloading {url}: {e}")


def queue_worker_loop(q: queue.Queue, executor: concurrent.futures.ThreadPoolExecutor, cookies_file_path: str):
    """Continuously monitors the queue and submits download jobs to the thread pool."""
    print(f"[QUEUE] Worker loop started with {MAX_WORKERS} max concurrent threads.")
    while True:
        try:
            # Get a URL from the queue 
            url = q.get(timeout=1) 
            
            # Submit the download task to the thread pool executor
            executor.submit(download_youtube_url, url, cookies_file_path)
            
            # Signal that the item has been pulled from the queue
            q.task_done()
            
        except queue.Empty:
            # The queue is empty, loop again
            pass
        except Exception as e:
            print(f"[QUEUE ERROR] An error occurred in the worker loop: {e}")

# --- Main execution loop ---

if __name__ == "__main__":
    
    # Initialize the thread pool executor for running downloads
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
    
    # Cookies path from environment variable
    COOKIES_FILE_PATH = os.environ.get("DOWNLOADER_COOKIES_PATH")
    
    # If the environment variable is not set will fall back
    if not COOKIES_FILE_PATH:
        print("\nTo download age-restricted or private videos, please provide a cookies file path.")
        
        # Interactive fallback
        interactive_input = input("Enter path to Netscape-format cookies file (or press Enter): ").strip()
        if interactive_input:
            COOKIES_FILE_PATH = interactive_input

    # Check existence only if a path was provided
    if COOKIES_FILE_PATH and not os.path.exists(COOKIES_FILE_PATH):
        print(f"[WARNING] Cookies file not found at: {COOKIES_FILE_PATH}. Continuing without authentication.")
        COOKIES_FILE_PATH = None
    elif COOKIES_FILE_PATH:
        print(f"[INFO] Using cookies file: {COOKIES_FILE_PATH}")
    else:
        print("[INFO] Continuing without authentication (cookies).")
        
    print("-" * 50)
    
    # Start the background thread that pulls items from the queue
    worker_thread = threading.Thread(
        target=queue_worker_loop, 
        # The COOKIES_FILE_PATH global variable
        args=(download_queue, executor, COOKIES_FILE_PATH), 
        daemon=True
    )
    worker_thread.start()
    
    print("-" * 50)
    print(f"MP3 Downloader Utility (Queue enabled, {MAX_WORKERS} concurrent downloads)")
    print("Commands: Enter URL | 'q' to quit | 'w' to wait for queue to clear")
    print("-" * 50)

    while True:
        try:
            # Display current queue size in the prompt
            user_input = input(f"Enter YouTube URL (Queue size: {download_queue.qsize()}) > ").strip()
            
            if user_input.lower() == 'q':
                print("\n[INFO] Exiting program...")
                break
            
            if user_input.lower() == 'w':
                if download_queue.empty():
                    print("[INFO] Queue is already empty. No downloads waiting.")
                else:
                    # Wait for all submitted tasks in the queue to be completed
                    print("[INFO] Waiting for all pending downloads to complete...")
                    download_queue.join()
                    print("[INFO] All queued downloads finished.")
                continue

            if user_input:
                # Add the URL to the queue immediately and continue
                download_queue.put(user_input)
                print(f"[INFO] URL added to queue. Current queue size: {download_queue.qsize()}")

        except KeyboardInterrupt:
            print("\nProgram interrupted. Exiting.")
            break
        except Exception as e:
            print(f"An error occurred in the main loop: {e}")

    print("\n[INFO] Shutting down workers. Waiting for running downloads to finish...")
    executor.shutdown(wait=True)
    print("[INFO] Download utility shut down.")