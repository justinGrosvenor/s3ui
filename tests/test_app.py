"""Smoke tests for app startup."""


def test_main_can_be_imported():
    """main() function is importable."""
    from s3ui.app import main

    assert callable(main)


def test_main_window_creates(qtbot):
    """MainWindow can be instantiated without error."""
    from s3ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    assert window.windowTitle() == "S3UI"
    assert window.minimumWidth() == 900
    assert window.minimumHeight() == 600


def test_constants_defined():
    """Core constants are set."""
    from s3ui.constants import APP_DIR, APP_NAME, DB_PATH, KEYRING_SERVICE

    assert APP_NAME == "S3UI"
    assert KEYRING_SERVICE == "s3ui"
    assert APP_DIR.name == ".s3ui"
    assert DB_PATH.name == "s3ui.db"


def test_logging_setup(tmp_path, monkeypatch):
    """setup_logging creates log directory and configures logger."""
    import logging

    import s3ui.constants as constants

    log_dir = tmp_path / "test_logs"
    log_file = log_dir / "s3ui.log"
    monkeypatch.setattr(constants, "LOG_DIR", log_dir)
    monkeypatch.setattr(constants, "LOG_FILE", log_file)

    from s3ui.logging_setup import setup_logging

    setup_logging()

    assert log_dir.exists()
    logger = logging.getLogger("s3ui")
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) > 0
