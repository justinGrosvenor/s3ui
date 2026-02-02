import logging
import sys

from s3ui.constants import APP_DIR, APP_NAME


def _set_macos_process_name() -> None:
    """Set the macOS menu bar and Dock name to APP_NAME instead of 'Python'.

    Without an .app bundle, macOS falls back to the executable name.  Setting
    CFBundleName in the main bundle's info dictionary fixes this for PyQt6 apps.
    Must be called *before* QApplication is created.
    """
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        NSBundle = objc.objc_getClass(b"NSBundle")
        bundle = objc.objc_msgSend(NSBundle, objc.sel_registerName(b"mainBundle"))
        info = objc.objc_msgSend(bundle, objc.sel_registerName(b"infoDictionary"))

        NSString = objc.objc_getClass(b"NSString")
        sel_str = objc.sel_registerName(b"stringWithUTF8String:")
        objc.objc_msgSend.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
        ]
        key = objc.objc_msgSend(NSString, sel_str, b"CFBundleName")
        val = objc.objc_msgSend(NSString, sel_str, APP_NAME.encode())

        objc.objc_msgSend.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        sel_set = objc.sel_registerName(b"setObject:forKey:")
        objc.objc_msgSend(info, sel_set, val, key)
    except Exception:
        pass  # Not critical — menu bar will just say "Python"


def main() -> None:
    # Ensure app directory exists before anything else
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # Set up logging before any other imports that might log
    from s3ui.logging_setup import setup_logging

    setup_logging()
    logger = logging.getLogger("s3ui.app")
    logger.info("Starting %s", APP_NAME)

    # Must be called before QApplication is created
    _set_macos_process_name()

    from PyQt6.QtCore import QLockFile
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    # Set application icon (window, dock, tray)
    from importlib.resources import files

    icon_path = str(files("s3ui.resources").joinpath("s3ui.png"))
    app.setWindowIcon(QIcon(icon_path))

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
