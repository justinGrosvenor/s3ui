import logging
import sys

from s3ui.constants import APP_DIR, APP_NAME


def main() -> None:
    # Ensure app directory exists before anything else
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # Set up logging before any other imports that might log
    from s3ui.logging_setup import setup_logging

    setup_logging()
    logger = logging.getLogger("s3ui.app")
    logger.info("Starting %s", APP_NAME)

    from PyQt6.QtCore import QLockFile
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # Single-instance check
    lock_file = QLockFile(str(APP_DIR / "s3ui.lock"))
    if not lock_file.tryLock(100):
        logger.warning("Another instance is already running, exiting")
        sys.exit(0)

    # Initialize database
    from s3ui.db.database import Database

    db = Database()

    from s3ui.main_window import MainWindow

    window = MainWindow(db=db)
    window.show()
    logger.info("Window shown, entering event loop")

    exit_code = app.exec()
    db.close()
    lock_file.unlock()
    logger.info("Exiting with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
