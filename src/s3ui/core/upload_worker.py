"""Upload worker — handles single and multipart uploads as a QRunnable."""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from s3ui.constants import (
    DEFAULT_PART_SIZE,
    HUGE_PART_SIZE,
    LARGE_PART_SIZE,
    MAX_RETRY_ATTEMPTS,
    MULTIPART_THRESHOLD,
)

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.upload_worker")


def select_part_size(file_size: int) -> int:
    """Choose part size based on file size to stay under 10,000 parts."""
    if file_size <= 50 * 1024**3:  # ≤50 GB
        return DEFAULT_PART_SIZE
    elif file_size <= 500 * 1024**3:  # ≤500 GB
        return LARGE_PART_SIZE
    else:
        return HUGE_PART_SIZE


class UploadWorkerSignals(QObject):
    progress = pyqtSignal(int, int, int)  # transfer_id, bytes_done, total
    speed = pyqtSignal(int, float)  # transfer_id, bytes_per_sec
    finished = pyqtSignal(int)  # transfer_id
    failed = pyqtSignal(int, str, str)  # transfer_id, user_msg, detail


class UploadWorker(QRunnable):
    """Uploads a file to S3, with multipart support and resume."""

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
        self.signals = UploadWorkerSignals()
        self.transfer_id = transfer_id
        self._s3 = s3_client
        self._db = db
        self._bucket = bucket
        self._pause = pause_event
        self._cancel = cancel_event

        # Speed tracking
        self._speed_window: list[tuple[float, int]] = []
        self._last_speed_emit = 0.0

    def run(self) -> None:
        try:
            self._do_upload()
        except Exception as e:
            import traceback

            logger.error("Upload %d failed: %s", self.transfer_id, e)
            try:
                self._mark_failed(str(e))
            except Exception:
                logger.exception(
                    "Failed to mark upload %d as failed", self.transfer_id
                )
            self.signals.failed.emit(
                self.transfer_id, str(e), traceback.format_exc()
            )

    def _do_upload(self) -> None:
        row = self._db.fetchone(
            "SELECT * FROM transfers WHERE id = ?", (self.transfer_id,)
        )
        if not row:
            self.signals.failed.emit(
                self.transfer_id, "Transfer record not found.", ""
            )
            return

        from pathlib import Path

        local_path = Path(row["local_path"])
        if not local_path.exists():
            self._mark_failed("Source file no longer exists.")
            self.signals.failed.emit(
                self.transfer_id,
                "Source file no longer exists.",
                str(local_path),
            )
            return

        object_key = row["object_key"]
        file_size = local_path.stat().st_size

        # Update total_bytes if not set
        if row["total_bytes"] is None or row["total_bytes"] != file_size:
            self._db.execute(
                "UPDATE transfers SET total_bytes = ? WHERE id = ?",
                (file_size, self.transfer_id),
            )

        self._db.execute(
            "UPDATE transfers SET status = 'in_progress', updated_at = datetime('now') "
            "WHERE id = ?",
            (self.transfer_id,),
        )

        if file_size < MULTIPART_THRESHOLD:
            self._single_upload(local_path, object_key, file_size)
        else:
            self._multipart_upload(local_path, object_key, file_size, row)

    def _single_upload(self, local_path, object_key: str, file_size: int) -> None:
        data = local_path.read_bytes()
        self._s3.put_object(self._bucket, object_key, data)
        self._complete(file_size)

    def _multipart_upload(self, local_path, object_key: str, file_size: int, row) -> None:
        part_size = select_part_size(file_size)
        num_parts = math.ceil(file_size / part_size)
        upload_id = row["upload_id"]

        # Initiate or resume
        if not upload_id:
            upload_id = self._s3.create_multipart_upload(self._bucket, object_key)
            self._db.execute(
                "UPDATE transfers SET upload_id = ? WHERE id = ?",
                (upload_id, self.transfer_id),
            )
            # Create part records
            for i in range(num_parts):
                offset = i * part_size
                size = min(part_size, file_size - offset)
                self._db.execute(
                    "INSERT OR IGNORE INTO transfer_parts "
                    "(transfer_id, part_number, offset, size) VALUES (?, ?, ?, ?)",
                    (self.transfer_id, i + 1, offset, size),
                )
        else:
            # Resuming: reconcile with S3
            s3_parts = self._s3.list_parts(self._bucket, object_key, upload_id)
            s3_confirmed = {p["PartNumber"] for p in s3_parts}
            for part_num in s3_confirmed:
                self._db.execute(
                    "UPDATE transfer_parts SET status = 'completed' "
                    "WHERE transfer_id = ? AND part_number = ?",
                    (self.transfer_id, part_num),
                )

        # Upload pending parts
        pending = self._db.fetchall(
            "SELECT * FROM transfer_parts WHERE transfer_id = ? AND status != 'completed' "
            "ORDER BY part_number",
            (self.transfer_id,),
        )

        bytes_done = self._get_transferred()
        parts_for_complete = self._get_completed_parts()

        with open(local_path, "rb") as f:
            for part_row in pending:
                if self._cancel.is_set():
                    self._do_cancel(object_key, upload_id)
                    return
                if self._pause.is_set():
                    self._do_pause()
                    return

                part_num = part_row["part_number"]
                offset = part_row["offset"]
                size = part_row["size"]

                f.seek(offset)
                data = f.read(size)
                etag = self._upload_part_with_retry(
                    object_key, upload_id, part_num, data
                )
                if etag is None:
                    return  # failed signal already emitted

                self._db.execute(
                    "UPDATE transfer_parts SET status = 'completed', etag = ? "
                    "WHERE transfer_id = ? AND part_number = ?",
                    (etag, self.transfer_id, part_num),
                )

                bytes_done += size
                self._db.execute(
                    "UPDATE transfers SET transferred = ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (bytes_done, self.transfer_id),
                )
                self.signals.progress.emit(self.transfer_id, bytes_done, file_size)
                self._update_speed(size)
                parts_for_complete.append(
                    {"ETag": etag, "PartNumber": part_num}
                )

        # Complete
        all_parts = sorted(
            self._get_all_completed_parts(), key=lambda p: p["PartNumber"]
        )
        self._s3.complete_multipart_upload(
            self._bucket, object_key, upload_id, all_parts
        )
        self._complete(file_size)

    def _upload_part_with_retry(
        self, key: str, upload_id: str, part_num: int, data: bytes
    ) -> str | None:
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                return self._s3.upload_part(
                    self._bucket, key, upload_id, part_num, data
                )
            except Exception as e:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        "Upload part %d attempt %d failed, retrying in %.1fs: %s",
                        part_num, attempt + 1, delay, e,
                    )
                    time.sleep(delay)
                else:
                    self._mark_failed(str(e))
                    self.signals.failed.emit(
                        self.transfer_id,
                        f"Upload failed after {MAX_RETRY_ATTEMPTS} attempts.",
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
        logger.info("Upload %d completed", self.transfer_id)

    def _mark_failed(self, msg: str) -> None:
        self._db.execute(
            "UPDATE transfers SET status = 'failed', error_message = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (msg, self.transfer_id),
        )

    def _do_cancel(self, key: str, upload_id: str) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._s3.abort_multipart_upload(self._bucket, key, upload_id)
        self._db.execute(
            "UPDATE transfers SET status = 'cancelled', updated_at = datetime('now') "
            "WHERE id = ?",
            (self.transfer_id,),
        )
        logger.info("Upload %d cancelled", self.transfer_id)

    def _do_pause(self) -> None:
        self._db.execute(
            "UPDATE transfers SET status = 'paused', updated_at = datetime('now') "
            "WHERE id = ?",
            (self.transfer_id,),
        )
        logger.info("Upload %d paused", self.transfer_id)

    def _get_transferred(self) -> int:
        row = self._db.fetchone(
            "SELECT COALESCE(SUM(size), 0) as done FROM transfer_parts "
            "WHERE transfer_id = ? AND status = 'completed'",
            (self.transfer_id,),
        )
        return row["done"]

    def _get_completed_parts(self) -> list[dict]:
        rows = self._db.fetchall(
            "SELECT part_number, etag FROM transfer_parts "
            "WHERE transfer_id = ? AND status = 'completed'",
            (self.transfer_id,),
        )
        return [{"ETag": r["etag"], "PartNumber": r["part_number"]} for r in rows]

    def _get_all_completed_parts(self) -> list[dict]:
        return self._get_completed_parts()

    def _update_speed(self, chunk_bytes: int) -> None:
        now = time.monotonic()
        self._speed_window.append((now, chunk_bytes))
        # Keep 3-second window
        self._speed_window = [
            (t, b) for t, b in self._speed_window if now - t <= 3.0
        ]
        if now - self._last_speed_emit >= 0.5 and self._speed_window:
            window_time = now - self._speed_window[0][0]
            if window_time > 0:
                total_bytes = sum(b for _, b in self._speed_window)
                bps = total_bytes / window_time
                self.signals.speed.emit(self.transfer_id, bps)
            self._last_speed_emit = now


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: 0s, ~1s, ~4s."""
    if attempt == 0:
        return 0.0
    base = 4 ** (attempt - 1)  # 1, 4
    jitter_max = base * 0.5
    return base + random.uniform(0, jitter_max)
