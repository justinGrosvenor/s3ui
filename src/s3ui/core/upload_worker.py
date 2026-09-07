"""Upload worker — handles single and multipart uploads as a QRunnable."""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from s3transfer.utils import ReadFileChunk

from s3ui.constants import (
    DEFAULT_PART_SIZE,
    HUGE_PART_SIZE,
    LARGE_PART_SIZE,
    MAX_RETRY_ATTEMPTS,
    MULTIPART_THRESHOLD,
)
from s3ui.core.s3_client import S3ClientError

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
        # Round up to MiB while accommodating the full 10,000-part ceiling.
        mib = 1024**2
        size = max(HUGE_PART_SIZE, math.ceil(file_size / (10_000 * mib)) * mib)
        if size > 5 * 1024**3:
            raise ValueError("File exceeds the supported multipart upload size.")
        return size


class UploadWorkerSignals(QObject):
    progress = pyqtSignal(int, object, object)  # transfer_id, bytes_done, total
    speed = pyqtSignal(int, float)  # transfer_id, bytes_per_sec
    finished = pyqtSignal(int)  # transfer_id
    failed = pyqtSignal(int, str, str)  # transfer_id, user_msg, detail
    stopped = pyqtSignal(int)  # transfer_id — worker exited due to pause/cancel


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
                logger.exception("Failed to mark upload %d as failed", self.transfer_id)
            self.signals.failed.emit(self.transfer_id, str(e), traceback.format_exc())

    def _do_upload(self) -> None:
        row = self._db.fetchone("SELECT * FROM transfers WHERE id = ?", (self.transfer_id,))
        if not row:
            self.signals.failed.emit(self.transfer_id, "Transfer record not found.", "")
            return

        from pathlib import Path

        object_key = row["object_key"]
        if self._check_control(object_key, row["upload_id"]):
            return
        local_path = Path(row["local_path"])
        if not local_path.is_file():
            self._mark_failed("Source file no longer exists.")
            self.signals.failed.emit(
                self.transfer_id,
                "Source file no longer exists.",
                str(local_path),
            )
            return

        stat = local_path.stat()
        file_size = stat.st_size
        self._source_identity = (file_size, stat.st_mtime_ns)
        upload_id = row["upload_id"]
        if upload_id and (
            row["source_mtime_ns"] != stat.st_mtime_ns or row["total_bytes"] != file_size
        ):
            # A changed or legacy source cannot safely reuse already uploaded bytes.
            self._s3.abort_multipart_upload(self._bucket, object_key, upload_id)
            self._reset_multipart()
            upload_id = None

        self._db.execute(
            "UPDATE transfers SET total_bytes = ?, source_mtime_ns = ? WHERE id = ?",
            (file_size, stat.st_mtime_ns, self.transfer_id),
        )

        self._db.execute(
            "UPDATE transfers SET status = 'in_progress', updated_at = datetime('now') "
            "WHERE id = ?",
            (self.transfer_id,),
        )

        if file_size < MULTIPART_THRESHOLD:
            self._single_upload(local_path, object_key, file_size)
        else:
            self._multipart_upload(local_path, object_key, file_size, upload_id)

    def _single_upload(self, local_path, object_key: str, file_size: int) -> None:
        data = local_path.read_bytes()
        self._validate_source(local_path)
        if self._check_control(object_key):
            return
        self._s3.put_object(self._bucket, object_key, data)
        self._complete(file_size)

    def _reset_multipart(self) -> None:
        self._db.execute(
            "UPDATE transfers SET upload_id = NULL, transferred = 0 WHERE id = ?",
            (self.transfer_id,),
        )
        self._db.execute("DELETE FROM transfer_parts WHERE transfer_id = ?", (self.transfer_id,))

    def _validate_source(self, local_path) -> None:
        stat = local_path.stat()
        if (stat.st_size, stat.st_mtime_ns) != self._source_identity:
            raise RuntimeError("Source file changed during upload. Retry to upload the new file.")

    def _check_control(self, key: str, upload_id: str | None = None) -> bool:
        if self._cancel.is_set():
            self._do_cancel(key, upload_id)
            return True
        if self._pause.is_set():
            self._do_pause()
            return True
        return False

    def _multipart_upload(self, local_path, object_key: str, file_size: int, upload_id) -> None:
        part_size = select_part_size(file_size)
        num_parts = math.ceil(file_size / part_size)
        s3_parts = []
        if upload_id:
            try:
                s3_parts = self._s3.list_parts(self._bucket, object_key, upload_id)
            except S3ClientError as exc:
                if exc.code != "NoSuchUpload":
                    raise
                # The server expired/aborted the session. Start a fresh one.
                self._reset_multipart()
                upload_id = None
        if not upload_id:
            upload_id = self._s3.create_multipart_upload(self._bucket, object_key)
            self._db.execute(
                "UPDATE transfers SET upload_id = ? WHERE id = ?",
                (upload_id, self.transfer_id),
            )

        # Rebuild every expected part, even if the previous process stopped
        # between saving the upload ID and creating all the local part rows.
        # S3 is authoritative: absent or wrong-sized remote parts are reuploaded.
        remote = {part["PartNumber"]: part for part in s3_parts}
        records = []
        for i in range(num_parts):
            offset = i * part_size
            size = min(part_size, file_size - offset)
            part = remote.get(i + 1)
            etag = part["ETag"] if part and part["Size"] == size else None
            records.append(
                (self.transfer_id, i + 1, offset, size, etag, "completed" if etag else "pending")
            )
        self._db.executemany(
            "INSERT INTO transfer_parts (transfer_id, part_number, offset, size, etag, status) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(transfer_id, part_number) DO UPDATE SET "
            "offset = excluded.offset, size = excluded.size, etag = excluded.etag, "
            "status = excluded.status",
            records,
        )
        self._db.execute(
            "DELETE FROM transfer_parts WHERE transfer_id = ? AND part_number > ?",
            (self.transfer_id, num_parts),
        )
        pending = self._db.fetchall(
            "SELECT * FROM transfer_parts WHERE transfer_id = ? AND status != 'completed' "
            "ORDER BY part_number",
            (self.transfer_id,),
        )
        bytes_done = self._get_transferred()
        self._db.execute(
            "UPDATE transfers SET transferred = ? WHERE id = ?", (bytes_done, self.transfer_id)
        )
        self.signals.progress.emit(self.transfer_id, bytes_done, file_size)

        for part_row in pending:
            if self._check_control(object_key, upload_id):
                return
            self._validate_source(local_path)
            part_num = part_row["part_number"]
            size = part_row["size"]
            # Stream the bounded part through botocore instead of allocating
            # up to several GiB per worker for large source files.
            with ReadFileChunk.from_filename(local_path, part_row["offset"], size) as data:
                if len(data) != size:
                    raise RuntimeError("Source file was truncated during upload.")
                etag = self._upload_part_with_retry(object_key, upload_id, part_num, data)
            if etag is None:
                return
            bytes_done += size
            self._db.execute_batch(
                [
                    (
                        "UPDATE transfer_parts SET status = 'completed', etag = ? "
                        "WHERE transfer_id = ? AND part_number = ?",
                        (etag, self.transfer_id, part_num),
                    ),
                    (
                        "UPDATE transfers SET transferred = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (bytes_done, self.transfer_id),
                    ),
                ]
            )
            self.signals.progress.emit(self.transfer_id, bytes_done, file_size)
            self._update_speed(size)

        # A stop request during the last part must not publish the object.
        if self._check_control(object_key, upload_id):
            return
        self._validate_source(local_path)
        all_parts = sorted(self._get_completed_parts(), key=lambda p: p["PartNumber"])
        if len(all_parts) != num_parts or self._get_transferred() != file_size:
            raise RuntimeError("Multipart upload is incomplete; retry to reconcile its parts.")
        self._s3.complete_multipart_upload(self._bucket, object_key, upload_id, all_parts)
        self._complete(file_size)

    def _upload_part_with_retry(
        self, key: str, upload_id: str, part_num: int, data: ReadFileChunk
    ) -> str | None:
        for attempt in range(MAX_RETRY_ATTEMPTS):
            if self._check_control(key, upload_id):
                return None
            try:
                data.seek(0)
                return self._s3.upload_part(self._bucket, key, upload_id, part_num, data)
            except Exception as e:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        "Upload part %d attempt %d failed, retrying in %.1fs: %s",
                        part_num,
                        attempt + 1,
                        delay,
                        e,
                    )
                    self._cancel.wait(delay)
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
            "UPDATE transfers SET status = 'completed', transferred = ?, upload_id = NULL, "
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

    def _do_cancel(self, key: str, upload_id: str | None) -> None:
        if upload_id:
            try:
                self._s3.abort_multipart_upload(self._bucket, key, upload_id)
            except Exception:
                logger.warning("Could not abort upload %s; retained for later cleanup", upload_id)
            else:
                self._reset_multipart()
        self._db.execute(
            "UPDATE transfers SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?",
            (self.transfer_id,),
        )
        self.signals.stopped.emit(self.transfer_id)
        logger.info("Upload %d cancelled", self.transfer_id)

    def _do_pause(self) -> None:
        self._db.execute(
            "UPDATE transfers SET status = 'paused', updated_at = datetime('now') "
            "WHERE id = ? AND status IN ('queued', 'in_progress', 'paused')",
            (self.transfer_id,),
        )
        self.signals.stopped.emit(self.transfer_id)
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

    def _update_speed(self, chunk_bytes: int) -> None:
        now = time.monotonic()
        self._speed_window.append((now, chunk_bytes))
        # Keep 3-second window
        self._speed_window = [(t, b) for t, b in self._speed_window if now - t <= 3.0]
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
