"""Transfer engine — manages queue of uploads and downloads."""

from __future__ import annotations

import logging
import threading
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from s3ui.core.download_worker import DownloadWorker
from s3ui.core.upload_worker import UploadWorker

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.transfers")

# Per-database registry of transfer IDs with a live worker in ANY engine.
# Guards against duplicate workers writing to the same file when an engine is
# replaced (bucket/profile switch) or a transfer is resumed before its paused
# worker has exited. Transfer IDs are only unique within one database.
_LIVE_TRANSFERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_LIVE_LOCK = threading.Lock()


def _live_transfers(db: Database) -> set[int]:
    """Return the shared live-transfer set for a database."""
    with _LIVE_LOCK:
        live = _LIVE_TRANSFERS.get(db)
        if live is None:
            live = set()
            _LIVE_TRANSFERS[db] = live
        return live


class TransferEngine(QObject):
    """Manages the transfer queue and worker pool."""

    transfer_progress = pyqtSignal(int, object, object)  # transfer_id, bytes_done, total
    transfer_speed = pyqtSignal(int, float)  # transfer_id, bytes_per_sec
    transfer_status_changed = pyqtSignal(int, str)  # transfer_id, new_status
    transfer_error = pyqtSignal(int, str, str)  # transfer_id, user_msg, detail
    transfer_finished = pyqtSignal(int)  # transfer_id
    drained = pyqtSignal()  # emitted after shutdown() once all workers have exited

    def __init__(
        self,
        s3_client: S3Client,
        db: Database,
        bucket: str,
        max_workers: int = 4,
        profile: str | None = None,
    ) -> None:
        super().__init__()
        self._s3 = s3_client
        self._db = db
        self._bucket = bucket
        self._profile = profile
        self._bucket_id = None
        self._resolve_bucket_id()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_workers)

        # Per-transfer control events
        self._pause_events: dict[int, threading.Event] = {}
        self._cancel_events: dict[int, threading.Event] = {}
        self._active: set[int] = set()
        self._resume_requested: set[int] = set()
        self._live = _live_transfers(db)
        self._paused_global = False
        self._shutdown = False

    def enqueue(self, transfer_id: int) -> None:
        """Submit a transfer to the worker pool."""
        if self._shutdown or self._paused_global:
            return
        row = self._db.fetchone("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
        if not row:
            logger.warning("Cannot enqueue transfer %d: not found", transfer_id)
            return
        if row["bucket_id"] != self._resolve_bucket_id() or row["status"] != "queued":
            return
        if len(self._active) >= self._pool.maxThreadCount():
            return  # Keep waiting work in SQLite, where pause/cancel can reach it.

        with _LIVE_LOCK:
            if transfer_id in self._live:
                logger.info(
                    "Transfer %d already has a live worker; not starting another",
                    transfer_id,
                )
                return
            self._live.add(transfer_id)

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
        worker.signals.stopped.connect(self._on_stopped)

        self._pool.start(worker)
        self.transfer_status_changed.emit(transfer_id, "in_progress")
        logger.info("Enqueued transfer %d (%s)", transfer_id, row["direction"])

    def pause(self, transfer_id: int) -> None:
        """Pause a running transfer."""
        if not self.handles(transfer_id):
            return
        self._resume_requested.discard(transfer_id)
        evt = self._pause_events.get(transfer_id)
        if evt:
            evt.set()
        self._db.execute(
            "UPDATE transfers SET status = 'paused', updated_at = datetime('now') "
            "WHERE id = ? AND status IN ('queued', 'in_progress')",
            (transfer_id,),
        )
        self.transfer_status_changed.emit(transfer_id, "paused")

    def resume(self, transfer_id: int) -> None:
        """Resume a paused transfer by re-enqueuing it."""
        if self._shutdown or not self.handles(transfer_id):
            return
        if transfer_id in self._active:
            if self._pause_events[transfer_id].is_set():
                self._resume_requested.add(transfer_id)
            return  # Wait for the paused worker to close its file before restarting.
        self._db.execute(
            "UPDATE transfers SET status = 'queued', updated_at = datetime('now') "
            "WHERE id = ? AND status = 'paused'",
            (transfer_id,),
        )
        self.transfer_status_changed.emit(transfer_id, "queued")
        self.enqueue(transfer_id)

    def cancel(self, transfer_id: int) -> None:
        """Cancel a transfer."""
        if not self.handles(transfer_id):
            return
        self._resume_requested.discard(transfer_id)
        evt = self._cancel_events.get(transfer_id)
        if evt:
            evt.set()
        else:
            # No live worker (queued/paused) — mark the row directly so
            # restore_pending doesn't resurrect it later
            self._db.execute(
                "UPDATE transfers SET status = 'cancelled', updated_at = datetime('now') "
                "WHERE id = ? AND status IN ('queued', 'paused')",
                (transfer_id,),
            )
            self._pool.start(lambda: self._cleanup_cancelled_transfer(transfer_id))
        self.transfer_status_changed.emit(transfer_id, "cancelled")

    def _cleanup_cancelled_transfer(self, transfer_id: int) -> None:
        """Free resumable resources off the GUI thread when no live worker owns them."""
        row = self._db.fetchone(
            "SELECT * FROM transfers WHERE id = ? AND bucket_id = ? AND status = 'cancelled'",
            (transfer_id, self._resolve_bucket_id()),
        )
        if row is None:
            return
        try:
            if row["direction"] == "upload" and row["upload_id"]:
                self._s3.abort_multipart_upload(self._bucket, row["object_key"], row["upload_id"])
                self._db.execute(
                    "UPDATE transfers SET upload_id = NULL WHERE id = ?", (transfer_id,)
                )
            elif row["direction"] == "download":
                temp = Path(row["local_path"]).parent / f".s3ui-download-{transfer_id}.tmp"
                temp.unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to clean up cancelled transfer %d", transfer_id)

    def handles(self, transfer_id: int) -> bool:
        """Whether a transfer belongs to this engine's bucket and profile."""
        row = self._db.fetchone("SELECT bucket_id FROM transfers WHERE id = ?", (transfer_id,))
        return row is not None and row["bucket_id"] == self._resolve_bucket_id()

    def owns(self, transfer_id: int) -> bool:
        """True if this engine has a live worker for the transfer."""
        return transfer_id in self._active

    def pause_all(self) -> None:
        """Pause all active transfers."""
        self._paused_global = True
        rows = self._db.fetchall(
            "SELECT id FROM transfers WHERE bucket_id = ? AND status IN ('queued', 'in_progress')",
            (self._resolve_bucket_id(),),
        )
        self._resume_requested.clear()
        for event in self._pause_events.values():
            event.set()
        self._db.execute(
            "UPDATE transfers SET status = 'paused', updated_at = datetime('now') "
            "WHERE bucket_id = ? AND status IN ('queued', 'in_progress')",
            (self._resolve_bucket_id(),),
        )
        for row in rows:
            self.transfer_status_changed.emit(row["id"], "paused")

    def resume_all(self) -> None:
        """Resume all paused transfers."""
        self._paused_global = False
        rows = self._db.fetchall(
            "SELECT id FROM transfers WHERE status = 'paused' AND bucket_id = ?",
            (self._resolve_bucket_id(),),
        )
        self._resume_requested.update(
            tid
            for tid, event in self._pause_events.items()
            if event.is_set() and not self._cancel_events[tid].is_set()
        )
        self._db.execute(
            "UPDATE transfers SET status = 'queued', updated_at = datetime('now') "
            "WHERE bucket_id = ? AND status = 'paused'",
            (self._resolve_bucket_id(),),
        )
        for row in rows:
            self.transfer_status_changed.emit(row["id"], "queued")
        self._pick_next()

    def retry(self, transfer_id: int) -> None:
        """Retry a failed transfer."""
        if self._shutdown or not self.handles(transfer_id):
            return
        self._db.execute(
            "UPDATE transfers SET status = 'queued', retry_count = 0, "
            "error_message = NULL, updated_at = datetime('now') WHERE id = ? AND status = 'failed'",
            (transfer_id,),
        )
        self.enqueue(transfer_id)

    def restore_pending(self) -> None:
        """Restore this bucket's transfers that were interrupted by an app shutdown."""
        if self._resolve_bucket_id() is None:
            return
        rows = self._db.fetchall(
            "SELECT id, direction, local_path, status FROM transfers "
            "WHERE status IN ('queued', 'in_progress') AND bucket_id = ?",
            (self._bucket_id,),
        )
        with _LIVE_LOCK:
            live = set(self._live)
        for row in rows:
            if row["id"] in live:
                continue  # A previous engine's worker is still running it

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
        self._resume_requested.discard(transfer_id)
        self._cleanup(transfer_id)
        self.transfer_finished.emit(transfer_id)
        self._pick_next()

    def _on_failed(self, transfer_id: int, user_msg: str, detail: str) -> None:
        self._resume_requested.discard(transfer_id)
        self._cleanup(transfer_id)
        self.transfer_error.emit(transfer_id, user_msg, detail)
        self._pick_next()

    def _on_stopped(self, transfer_id: int) -> None:
        """Worker exited due to pause or cancel — free its slot."""
        self._cleanup(transfer_id)
        if transfer_id in self._resume_requested:
            self._resume_requested.discard(transfer_id)
            self.resume(transfer_id)
        self._pick_next()

    def _cleanup(self, transfer_id: int) -> None:
        self._active.discard(transfer_id)
        self._pause_events.pop(transfer_id, None)
        self._cancel_events.pop(transfer_id, None)
        with _LIVE_LOCK:
            self._live.discard(transfer_id)
        if self._shutdown and not self._active:
            self.drained.emit()

    def shutdown(self) -> None:
        """Stop accepting new work; running transfers finish naturally.

        In-flight workers keep their pause/cancel events here (route control
        via owns()), and `drained` is emitted once the last one exits.
        """
        self._shutdown = True
        self._paused_global = True
        if not self._active:
            self.drained.emit()

    def _resolve_bucket_id(self) -> int | None:
        """Return the bucket's DB id, re-querying if it wasn't created yet.

        Scoped by profile when one is given — same-named buckets under
        different profiles/endpoints are distinct namespaces and must not
        adopt each other's transfers.
        """
        if self._bucket_id is None:
            if self._profile is not None:
                row = self._db.fetchone(
                    "SELECT id FROM buckets WHERE name = ? AND profile = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (self._bucket, self._profile),
                )
            else:
                row = self._db.fetchone(
                    "SELECT id FROM buckets WHERE name = ? ORDER BY id DESC LIMIT 1",
                    (self._bucket,),
                )
            self._bucket_id = row["id"] if row else None
        return self._bucket_id

    def _pick_next(self) -> None:
        """Start the next queued transfer for this bucket if a slot is available."""
        if self._shutdown or self._paused_global or self._resolve_bucket_id() is None:
            return
        rows = self._db.fetchall(
            "SELECT id FROM transfers WHERE status = 'queued' AND bucket_id = ? "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (self._bucket_id, self._pool.maxThreadCount() + len(self._live)),
        )
        for row in rows:
            if len(self._active) >= self._pool.maxThreadCount():
                break
            if row["id"] not in self._live:
                self.enqueue(row["id"])

    def cleanup_orphaned_uploads(self) -> int:
        """Retry cleanup only for cancelled uploads recorded by this application.

        Age does not prove ownership. Unknown sessions may belong to another
        application and must never be aborted automatically.
        """
        rows = self._db.fetchall(
            "SELECT id, object_key, upload_id FROM transfers WHERE bucket_id = ? "
            "AND status = 'cancelled' AND upload_id IS NOT NULL",
            (self._resolve_bucket_id(),),
        )
        aborted = 0
        for row in rows:
            try:
                self._s3.abort_multipart_upload(self._bucket, row["object_key"], row["upload_id"])
            except Exception:
                logger.warning("Failed to clean up cancelled upload %s", row["upload_id"])
                continue
            self._db.execute(
                "UPDATE transfers SET upload_id = NULL WHERE id = ? AND status = 'cancelled'",
                (row["id"],),
            )
            aborted += 1
        return aborted
