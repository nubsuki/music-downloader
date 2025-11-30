import threading

# store the status of each URL.

download_statuses = {}

# This prevents race conditions between the web server thread and the download worker threads.
status_lock = threading.Lock()
