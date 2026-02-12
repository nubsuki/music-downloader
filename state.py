import threading
from datetime import datetime, timezone

download_statuses = {}

status_lock = threading.Lock()

log_entries = []
log_lock = threading.Lock()
log_sequence = 0
MAX_LOG_ENTRIES = 5000

playlist_lock = threading.Lock()


def add_log_line(message):
    global log_sequence
    if message is None:
        return
    with log_lock:
        log_sequence += 1
        log_entries.append(
            {
                "id": log_sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": message,
            }
        )
        overflow = len(log_entries) - MAX_LOG_ENTRIES
        if overflow > 0:
            del log_entries[:overflow]
