"""Transfer engine — manages queue of uploads and downloads."""

from __future__ import annotations

import datetime
import logging
import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from s3ui.core.download_worker import DownloadWorker
from s3ui.core.upload_worker import UploadWorker

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.transfers")


class TransferEngine(QObject):
    """Manages the transfer queue and worker pool."""

    transfer_progress = pyqtSignal(int, int, int)  # transfer_id, bytes_done, total
    transfer_speed = pyqtSignal(int, float)  # transfer_id, bytes_per_sec
    transfer_status_changed = pyqtSignal(int, str)  # transfer_id, new_status
    transfer_error = pyqtSignal(int, str, str)  # transfer_id, user_msg, detail
    transfer_finished = pyqtSignal(int)  # transfer_id

    def __init__(
        self,
        s3_client: S3Client,
        db: Database,
        bucket: str,
        max_workers: int = 4,
    ) -> None:
        super().__init__()
        self._s3 = s3_client
        self._db = db
        self._bucket = bucket
        row = self._db.fetchone(
            "SELECT id FROM buckets WHERE name = ? ORDER BY id DESC LIMIT 1",
            (bucket,),
        )
        self._bucket_id = row["id"] if row else None
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_workers)

        # Per-transfer control events
        self._pause_events: dict[int, threading.Event] = {}
        self._cancel_events: dict[int, threading.Event] = {}
        self._active: set[int] = set()
        self._paused_global = False

    def enqueue(self, transfer_id: int) -> None:
        """Submit a transfer to the worker pool."""
        row = self._db.fetchone("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
        if not row:
            logger.warning("Cannot enqueue transfer %d: not found", transfer_id)
            return

        pause_evt = threading.Event()
        cancel_evt = threading.Event()
        self._pause_events[transfer_id] = pause_evt
        self._cancel_events[transfer_id] = cancel_evt
        self._active.add(transfer_id)

        if row["direction"] == "upload":
            worker = UploadWorker(
                transfer_id,
                self._s3,
                self._db,
                self._bucket,
                pause_evt,
                cancel_evt,
            )
        else:
            worker = DownloadWorker(
                transfer_id,
                self._s3,
                self._db,
                self._bucket,
                pause_evt,
                cancel_evt,
            )

        # Connect signals
        worker.signals.progress.connect(self._on_progress)
        worker.signals.speed.connect(self._on_speed)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)

        self._pool.start(worker)
        self.transfer_status_changed.emit(transfer_id, "in_progress")
        logger.info("Enqueued transfer %d (%s)", transfer_id, row["direction"])

    def pause(self, transfer_id: int) -> None:
        """Pause a running transfer."""
        evt = self._pause_events.get(transfer_id)
        if evt:
            evt.set()
            self.transfer_status_changed.emit(transfer_id, "paused")

    def resume(self, transfer_id: int) -> None:
        """Resume a paused transfer by re-enqueuing it."""
        self._db.execute(
            "UPDATE transfers SET status = 'queued', updated_at = datetime('now') WHERE id = ?",
            (transfer_id,),
        )
        self.enqueue(transfer_id)

    def cancel(self, transfer_id: int) -> None:
        """Cancel a transfer."""
        evt = self._cancel_events.get(transfer_id)
        if evt:
            evt.set()
        self.transfer_status_changed.emit(transfer_id, "cancelled")

    def pause_all(self) -> None:
        """Pause all active transfers."""
        self._paused_global = True
        for tid in list(self._active):
            self.pause(tid)

    def resume_all(self) -> None:
        """Resume all paused transfers."""
        self._paused_global = False
        rows = self._db.fetchall(
            "SELECT id FROM transfers WHERE status = 'paused' AND bucket_id = "
            "(SELECT id FROM buckets WHERE name = ? LIMIT 1)",
            (self._bucket,),
        )
        for row in rows:
            self.resume(row["id"])

    def retry(self, transfer_id: int) -> None:
        """Retry a failed transfer."""
        self._db.execute(
            "UPDATE transfers SET status = 'queued', retry_count = 0, "
            "error_message = NULL, updated_at = datetime('now') WHERE id = ?",
            (transfer_id,),
        )
        self.enqueue(transfer_id)

    def restore_pending(self) -> None:
        """Restore transfers that were interrupted by an app shutdown."""
        rows = self._db.fetchall(
            "SELECT id, direction, local_path, status FROM transfers "
            "WHERE status IN ('queued', 'in_progress', 'paused')"
        )
        for row in rows:
            from pathlib import Path

            local = Path(row["local_path"])

            if row["direction"] == "upload" and not local.exists():
                self._db.execute(
                    "UPDATE transfers SET status = 'failed', "
                    "error_message = 'Source file no longer exists.', "
                    "updated_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )
                logger.warning("Transfer %d: source file missing: %s", row["id"], local)
                continue

            if row["direction"] == "download" and not local.parent.exists():
                self._db.execute(
                    "UPDATE transfers SET status = 'failed', "
                    "error_message = 'Destination directory no longer exists.', "
                    "updated_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )
                logger.warning("Transfer %d: dest dir missing: %s", row["id"], local.parent)
                continue

            # Reset in_progress to queued
            if row["status"] == "in_progress":
                self._db.execute(
                    "UPDATE transfers SET status = 'queued', "
                    "updated_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )

            self.enqueue(row["id"])
            logger.info("Restored transfer %d", row["id"])

    # --- Signal handlers ---

    def _on_progress(self, transfer_id: int, bytes_done: int, total: int) -> None:
        self.transfer_progress.emit(transfer_id, bytes_done, total)

    def _on_speed(self, transfer_id: int, bps: float) -> None:
        self.transfer_speed.emit(transfer_id, bps)

    def _on_finished(self, transfer_id: int) -> None:
        self._cleanup(transfer_id)
        self.transfer_finished.emit(transfer_id)
        self._pick_next()

    def _on_failed(self, transfer_id: int, user_msg: str, detail: str) -> None:
        self._cleanup(transfer_id)
        self.transfer_error.emit(transfer_id, user_msg, detail)
        self._pick_next()

    def _cleanup(self, transfer_id: int) -> None:
        self._active.discard(transfer_id)
        self._pause_events.pop(transfer_id, None)
        self._cancel_events.pop(transfer_id, None)

    def _pick_next(self) -> None:
        """Start the next queued transfer if a slot is available."""
        if self._paused_global:
            return
        if self._bucket_id is None:
            row = self._db.fetchone(
                "SELECT id FROM transfers WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            )
        else:
            row = self._db.fetchone(
                "SELECT id FROM transfers WHERE status = 'queued' AND bucket_id = ? "
                "ORDER BY created_at ASC LIMIT 1",
                (self._bucket_id,),
            )
        if row and row["id"] not in self._active:
            self.enqueue(row["id"])

    def cleanup_orphaned_uploads(self) -> int:
        """Abort orphaned multipart uploads on S3 not tracked in the database.

        Only aborts uploads older than 24 hours to avoid interfering with
        uploads started by other tools. Returns the number of aborted uploads.
        """
        try:
            s3_uploads = self._s3.list_multipart_uploads(self._bucket)
        except Exception:
            logger.warning("Failed to list multipart uploads for orphan cleanup")
            return 0

        # Collect known upload IDs from our database
        rows = self._db.fetchall("SELECT upload_id FROM transfers WHERE upload_id IS NOT NULL")
        known_ids = {r["upload_id"] for r in rows}

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)
        aborted = 0

        for upload in s3_uploads:
            uid = upload["UploadId"]
            if uid in known_ids:
                continue  # We own this one

            initiated = upload["Initiated"]
            # Make sure initiated is offset-aware for comparison
            if initiated.tzinfo is None:
                initiated = initiated.replace(tzinfo=datetime.UTC)

            if initiated < cutoff:
                try:
                    self._s3.abort_multipart_upload(self._bucket, upload["Key"], uid)
                    aborted += 1
                    logger.info(
                        "Aborted orphaned multipart upload: key=%s upload_id=%s",
                        upload["Key"],
                        uid,
                    )
                except Exception:
                    logger.warning(
                        "Failed to abort orphaned upload: key=%s upload_id=%s",
                        upload["Key"],
                        uid,
                    )
            else:
                logger.debug(
                    "Skipping recent orphaned upload: key=%s upload_id=%s (< 24h old)",
                    upload["Key"],
                    uid,
                )

        if aborted:
            logger.info("Orphan cleanup: aborted %d uploads", aborted)
        return aborted
