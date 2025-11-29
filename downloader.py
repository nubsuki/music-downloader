import os
import subprocess
import sys
from urllib.parse import urlparse

# locate the local ffmpeg folder
try:
    script_path = os.path.abspath(__file__)
except NameError:
    script_path = os.path.abspath(sys.argv[0])

script_dir = os.path.dirname(script_path)

# Path to the local ffmpeg binary directory
local_ffmpeg_bin_dir = os.path.join(script_dir, "ffmpeg", "bin")

print(f"Script Directory: {script_dir}")
print(f"Expected FFmpeg Bin Path: {local_ffmpeg_bin_dir}")

def download_youtube_url(url: str, output_path: str = "downloads"):
    """
    Downloads a video from a given YouTube URL using yt-dlp,
    specifying a local path for FFmpeg, and converts it to MP3 with album art.
    """
    if not urlparse(url).scheme:
        print("Error: Invalid URL format. Please ensure the URL includes http:// or https://")
        return

    # Create the output directory if it doesn't exist
    os.makedirs(output_path, exist_ok=True)

    # --- yt-dlp Command Construction for MP3 ---

    command = [
        "yt-dlp",
        # Point yt-dlp to the local ffmpeg binaries
        f"--ffmpeg-location={local_ffmpeg_bin_dir}",
        
        # Audio Extraction Flags
        "-x",                           # Extract audio stream
        "--audio-format", "mp3",        # Convert extracted audio to MP3
        "--embed-metadata",             # Embed song metadata
        "--embed-thumbnail",            # Embed video thumbnail as album art

        # Output template: 'downloads/Video Title.mp3'
        "-o", os.path.join(output_path, "%(title)s.%(ext)s"),
        # The target URL
        url
    ]

    print("\nStarting MP3 download and conversion with embedded album art...")
    print(f"Command: {' '.join(command)}")

    try:
        # Run the command and stream output directly to console
        result = subprocess.run(
            command,
            check=True,  # Raise error on non-zero exit code
            capture_output=False,
            text=True
        )
        print("\nMP3 file created successfully.")

    except subprocess.CalledProcessError as e:
        print(f"\nError during download/conversion (yt-dlp exited with code {e.returncode}):")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
    except FileNotFoundError:
        print("\nError: 'yt-dlp' command not found.")
        print("Please ensure yt-dlp is installed and accessible in your system's PATH.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

# --- Main execution loop ---

if __name__ == "__main__":
    print("-" * 50)
    print("MP3 Downloader Utility")
    print("-" * 50)

    while True:
        try:
            user_input = input("Enter YouTube URL (or 'q' to quit): ").strip()
            if user_input.lower() == 'q':
                print("Exiting downloader.")
                break

            if user_input:
                download_youtube_url(user_input, output_path="downloads")

        except KeyboardInterrupt:
            print("\nProgram interrupted. Exiting.")
            break
        except Exception as e:
            print(f"An error occurred in the main loop: {e}")