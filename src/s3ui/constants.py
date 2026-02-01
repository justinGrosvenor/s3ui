from pathlib import Path

APP_NAME = "S3UI"
APP_DIR = Path.home() / ".s3ui"
DB_PATH = APP_DIR / "s3ui.db"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "s3ui.log"
KEYRING_SERVICE = "s3ui"

# Transfer defaults
DEFAULT_PART_SIZE = 8 * 1024 * 1024  # 8 MB
LARGE_PART_SIZE = 64 * 1024 * 1024  # 64 MB
HUGE_PART_SIZE = 512 * 1024 * 1024  # 512 MB
MULTIPART_THRESHOLD = 8 * 1024 * 1024  # 8 MB
MAX_CONCURRENT_TRANSFERS = 4
MAX_RETRY_ATTEMPTS = 3

# Cache defaults
LISTING_CACHE_MAX_ENTRIES = 30
LISTING_CACHE_STALE_SECONDS = 30.0

# UI defaults
MIN_WINDOW_WIDTH = 900
MIN_WINDOW_HEIGHT = 600
NAV_HISTORY_MAX = 50
TRANSFER_COALESCE_MS = 100

# Temp directory for quick-open downloads
TEMP_DIR = APP_DIR / "temp"

# Notification threshold
NOTIFY_SIZE_THRESHOLD = 100 * 1024 * 1024  # 100 MB

# Quick-open size threshold (files larger than this go through the transfer queue)
QUICK_OPEN_THRESHOLD = 10 * 1024 * 1024  # 10 MB

# Logging
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB per file
LOG_BACKUP_COUNT = 3
