import logging
from logging.handlers import RotatingFileHandler

from s3ui.constants import LOG_BACKUP_COUNT, LOG_DIR, LOG_FILE, MAX_LOG_SIZE


def setup_logging() -> None:
    """Configure rotating file logger for s3ui."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_LOG_SIZE,
        backupCount=LOG_BACKUP_COUNT,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    )
    root = logging.getLogger("s3ui")
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
