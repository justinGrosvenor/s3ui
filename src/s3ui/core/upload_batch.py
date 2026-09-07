"""Discover upload sources and persist queue entries away from the GUI thread."""

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


class UploadBatchWorker(QThread):
    batch_ready = pyqtSignal(list)  # transfer IDs; small batches keep GUI dispatch bounded
    failed = pyqtSignal(str)

    def __init__(self, db, bucket_id, prefix, paths, parent=None):
        super().__init__(parent)
        self._db = db
        self._bucket_id = bucket_id
        self._prefix = prefix
        self._paths = paths

    def run(self):
        try:
            batch = []
            for source in self._paths:
                root = Path(source)
                files = root.rglob("*") if root.is_dir() else [root]
                for path in files:
                    if self.isInterruptionRequested():
                        self._save_batch(batch)
                        return
                    if not path.is_file():
                        continue
                    name = path.relative_to(root.parent).as_posix()
                    stat = path.stat()
                    batch.append(
                        (
                            self._bucket_id,
                            self._prefix + name,
                            str(path),
                            stat.st_size,
                            stat.st_mtime_ns,
                        )
                    )
                    if len(batch) == 50:
                        self._save_batch(batch)
                        batch = []
            self._save_batch(batch)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self._db.close()

    def _save_batch(self, batch):
        if not batch:
            return
        sql = (
            "INSERT INTO transfers (bucket_id, object_key, direction, local_path, "
            "total_bytes, source_mtime_ns) VALUES (?, ?, 'upload', ?, ?, ?)"
        )
        ids = self._db.execute_batch([(sql, values) for values in batch])
        self.batch_ready.emit(ids)
