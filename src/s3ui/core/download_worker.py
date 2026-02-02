"""Download worker — handles single and ranged-GET downloads as a QRunnable."""

from __future__ import annotations

import logging
import random
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from s3ui.constants import DEFAULT_PART_SIZE, MAX_RETRY_ATTEMPTS, MULTIPART_THRESHOLD

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.download_worker")


class DownloadWorkerSignals(QObject):
    progress = pyqtSignal(int, int, int)  # transfer_id, bytes_done, total
    speed = pyqtSignal(int, float)  # transfer_id, bytes_per_sec
    finished = pyqtSignal(int)  # transfer_id
    failed = pyqtSignal(int, str, str)  # transfer_id, user_msg, detail


class DownloadWorker(QRunnable):
    """Downloads a file from S3, with ranged GET and resume support."""

    def __init__(
        self,
        transfer_id: int,
        s3_client: S3Client,
        db: Database,
        bucket: str,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.signals = DownloadWorkerSignals()
        self.transfer_id = transfer_id
        self._s3 = s3_client
        self._db = db
        self._bucket = bucket
        self._pause = pause_event
        self._cancel = cancel_event

        self._speed_window: list[tuple[float, int]] = []
        self._last_speed_emit = 0.0

    def run(self) -> None:
        try:
            self._do_download()
        except Exception as e:
            import traceback

            logger.error("Download %d failed: %s", self.transfer_id, e)
            try:
                self._mark_failed(str(e))
            except Exception:
                logger.exception("Failed to mark download %d as failed", self.transfer_id)
            self.signals.failed.emit(self.transfer_id, str(e), traceback.format_exc())

    def _do_download(self) -> None:
        row = self._db.fetchone("SELECT * FROM transfers WHERE id = ?", (self.transfer_id,))
        if not row:
            self.signals.failed.emit(self.transfer_id, "Transfer record not found.", "")
            return

        local_path = Path(row["local_path"])
        object_key = row["object_key"]

        # Validate destination directory
        if not local_path.parent.exists():
            self._mark_failed("Destination directory does not exist.")
            self.signals.failed.emit(
                self.transfer_id,
                "Destination directory does not exist.",
                str(local_path.parent),
            )
            return

        self._db.execute(
            "UPDATE transfers SET status = 'in_progress', updated_at = datetime('now') "
            "WHERE id = ?",
            (self.transfer_id,),
        )

        # Get object metadata
        item = self._s3.head_object(self._bucket, object_key)
        total_size = item.size or 0

        self._db.execute(
            "UPDATE transfers SET total_bytes = ? WHERE id = ?",
            (total_size, self.transfer_id),
        )

        temp_path = local_path.parent / f".s3ui-download-{self.transfer_id}.tmp"

        if total_size < MULTIPART_THRESHOLD:
            self._single_download(object_key, local_path, temp_path, total_size)
        else:
            self._ranged_download(object_key, local_path, temp_path, total_size)

    def _single_download(self, key: str, final_path: Path, temp_path: Path, total: int) -> None:
        body = self._s3.get_object(self._bucket, key)
        data = body.read()
        temp_path.write_bytes(data)
        temp_path.rename(final_path)
        self._complete(total)

    def _ranged_download(self, key: str, final_path: Path, temp_path: Path, total: int) -> None:
        chunk_size = DEFAULT_PART_SIZE

        # Resume from existing temp file
        offset = 0
        if temp_path.exists():
            offset = temp_path.stat().st_size
            self._db.execute(
                "UPDATE transfers SET transferred = ? WHERE id = ?",
                (offset, self.transfer_id),
            )

        cancelled = False
        paused = False
        with open(temp_path, "ab") as f:
            while offset < total:
                if self._cancel.is_set():
                    cancelled = True
                    break
                if self._pause.is_set():
                    paused = True
                    break

                end = min(offset + chunk_size - 1, total - 1)
                range_header = f"bytes={offset}-{end}"

                data = self._download_chunk_with_retry(key, range_header)
                if data is None:
                    return  # failed signal already emitted

                f.write(data)
                offset += len(data)

                self._db.execute(
                    "UPDATE transfers SET transferred = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (offset, self.transfer_id),
                )
                self.signals.progress.emit(self.transfer_id, offset, total)
                self._update_speed(len(data))

        if cancelled:
            self._do_cancel(temp_path)
            return
        if paused:
            self._do_pause(offset)
            return

        # Verify size
        actual_size = temp_path.stat().st_size
        if actual_size != total:
            msg = f"Size mismatch: expected {total}, got {actual_size}"
            self._mark_failed(msg)
            self.signals.failed.emit(self.transfer_id, msg, "")
            return

        # Atomic rename
        temp_path.rename(final_path)
        self._complete(total)

    def _download_chunk_with_retry(self, key: str, range_header: str) -> bytes | None:
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                body = self._s3.get_object(self._bucket, key, range_header)
                return body.read()
            except Exception as e:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        "Download chunk attempt %d failed, retrying in %.1fs: %s",
                        attempt + 1,
                        delay,
                        e,
                    )
                    time.sleep(delay)
                else:
                    self._mark_failed(str(e))
                    self.signals.failed.emit(
                        self.transfer_id,
                        f"Download failed after {MAX_RETRY_ATTEMPTS} attempts.",
                        str(e),
                    )
                    return None

    def _complete(self, total: int) -> None:
        self._db.execute(
            "UPDATE transfers SET status = 'completed', transferred = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (total, self.transfer_id),
        )
        self.signals.progress.emit(self.transfer_id, total, total)
        self.signals.finished.emit(self.transfer_id)
        logger.info("Download %d completed", self.transfer_id)

    def _mark_failed(self, msg: str) -> None:
        self._db.execute(
            "UPDATE transfers SET status = 'failed', error_message = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (msg, self.transfer_id),
        )

    def _do_cancel(self, temp_path: Path) -> None:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                logger.exception(
                    "Failed to remove temp file for transfer %d: %s",
                    self.transfer_id,
                    temp_path,
                )
        self._db.execute(
            "UPDATE transfers SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?",
            (self.transfer_id,),
        )
        logger.info("Download %d cancelled", self.transfer_id)

    def _do_pause(self, offset: int) -> None:
        self._db.execute(
            "UPDATE transfers SET status = 'paused', transferred = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (offset, self.transfer_id),
        )
        logger.info("Download %d paused at offset %d", self.transfer_id, offset)

    def _update_speed(self, chunk_bytes: int) -> None:
        now = time.monotonic()
        self._speed_window.append((now, chunk_bytes))
        self._speed_window = [(t, b) for t, b in self._speed_window if now - t <= 3.0]
        if now - self._last_speed_emit >= 0.5 and self._speed_window:
            window_time = now - self._speed_window[0][0]
            if window_time > 0:
                total_bytes = sum(b for _, b in self._speed_window)
                bps = total_bytes / window_time
                self.signals.speed.emit(self.transfer_id, bps)
            self._last_speed_emit = now


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter."""
    if attempt == 0:
        return 0.0
    base = 4 ** (attempt - 1)
    jitter_max = base * 0.5
    return base + random.uniform(0, jitter_max)
