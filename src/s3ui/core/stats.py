"""Bucket statistics collector."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QThread, pyqtSignal

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.stats")


@dataclass
class BucketSnapshot:
    """Results of a bucket scan."""

    bucket: str
    total_count: int = 0
    total_bytes: int = 0
    bytes_by_class: dict[str, int] = field(default_factory=dict)
    count_by_class: dict[str, int] = field(default_factory=dict)
    top_largest: list[dict] = field(default_factory=list)  # [{key, size}]


class _ScanSignals(QObject):
    progress = pyqtSignal(int)  # objects_counted
    complete = pyqtSignal(BucketSnapshot)
    error = pyqtSignal(str)


class StatsCollector(QThread):
    """Background thread that scans a bucket for statistics."""

    def __init__(
        self,
        s3_client: S3Client,
        bucket: str,
        db: Database | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.signals = _ScanSignals()
        self._s3 = s3_client
        self._bucket = bucket
        self._db = db
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            snapshot = BucketSnapshot(bucket=self._bucket)
            top_heap: list[tuple[int, str]] = []  # (size, key)

            paginator = self._s3._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self._bucket)

            for page in pages:
                if self._cancel.is_set():
                    return

                for obj in page.get("Contents", []):
                    size = obj.get("Size", 0)
                    storage_class = obj.get("StorageClass", "STANDARD")

                    snapshot.total_count += 1
                    snapshot.total_bytes += size
                    snapshot.bytes_by_class[storage_class] = (
                        snapshot.bytes_by_class.get(storage_class, 0) + size
                    )
                    snapshot.count_by_class[storage_class] = (
                        snapshot.count_by_class.get(storage_class, 0) + 1
                    )

                    # Track top 10 largest
                    import heapq

                    if len(top_heap) < 10:
                        heapq.heappush(top_heap, (size, obj["Key"]))
                    elif size > top_heap[0][0]:
                        heapq.heapreplace(top_heap, (size, obj["Key"]))

                self.signals.progress.emit(snapshot.total_count)

            snapshot.top_largest = [
                {"key": key, "size": size} for size, key in sorted(top_heap, reverse=True)
            ]

            # Save to database
            if self._db:
                import json

                self._db.execute(
                    "INSERT INTO bucket_snapshots "
                    "(bucket_id, total_objects, total_bytes, breakdown_json) "
                    "VALUES ("
                    "(SELECT id FROM buckets WHERE name = ? LIMIT 1), ?, ?, ?)",
                    (
                        self._bucket,
                        snapshot.total_count,
                        snapshot.total_bytes,
                        json.dumps(
                            {
                                "bytes_by_class": snapshot.bytes_by_class,
                                "count_by_class": snapshot.count_by_class,
                                "top_largest": snapshot.top_largest,
                            }
                        ),
                    ),
                )

            self.signals.complete.emit(snapshot)
            logger.info(
                "Scan complete for '%s': %d objects, %d bytes",
                self._bucket,
                snapshot.total_count,
                snapshot.total_bytes,
            )
        except Exception as e:
            logger.error("Scan failed for '%s': %s", self._bucket, e)
            self.signals.error.emit(str(e))
