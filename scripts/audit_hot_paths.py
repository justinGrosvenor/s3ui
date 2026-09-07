"""Repeatable local hot-path audit; uses temporary files/SQLite and no S3 account.

Run: .venv/bin/python scripts/audit_hot_paths.py
This measures local bookkeeping and buffering, not network throughput.
"""

import hashlib
import json
import platform
import tempfile
import time
import tracemalloc
from pathlib import Path

from s3transfer.utils import ReadFileChunk

from s3ui.db.database import Database


def measure(fn):
    tracemalloc.start()
    started = time.perf_counter()
    value = fn()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"seconds": round(elapsed, 4), "peak_mib": round(peak / 1024**2, 2)}, value


def main():
    with tempfile.TemporaryDirectory(prefix="s3ui-audit-") as directory:
        root = Path(directory)
        db = Database(root / "audit.db")
        bid = db.execute("INSERT INTO buckets (name, profile) VALUES ('audit', 'audit')").lastrowid
        tids = db.execute_batch(
            [
                (
                    "INSERT INTO transfers (bucket_id, object_key, direction, local_path) "
                    "VALUES (?, 'file', 'upload', '/unused')",
                    (bid,),
                )
                for _ in range(2)
            ]
        )
        sql = (
            "INSERT INTO transfer_parts (transfer_id, part_number, offset, size) "
            "VALUES (?, ?, ?, ?)"
        )
        part_count = 10_000
        commits = []
        db._get_conn().set_trace_callback(
            lambda sql: commits.append(sql) if sql == "COMMIT" else None
        )

        def prepare_before():
            for n in range(part_count):
                db.execute(sql, (tids[0], n + 1, n * 8, 8))

        old, _ = measure(prepare_before)
        old["commits"] = len(commits)
        commits.clear()
        new, _ = measure(
            lambda: db.executemany(sql, [(tids[1], n + 1, n * 8, 8) for n in range(part_count)])
        )
        new["commits"] = len(commits)
        db._get_conn().set_trace_callback(None)
        db.close()

        source = root / "part.bin"
        part_bytes = 64 * 1024**2
        with source.open("wb") as f:
            f.truncate(part_bytes)
        buffered, old_digest = measure(lambda: hashlib.sha256(source.read_bytes()).hexdigest())

        def stream():
            digest = hashlib.sha256()
            with ReadFileChunk.from_filename(source, 0, part_bytes) as body:
                while data := body.read(1024**2):
                    digest.update(data)
            return digest.hexdigest()

        streamed, new_digest = measure(stream)
        assert old_digest == new_digest
        print(
            json.dumps(
                {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "multipart_prepare": {"parts": part_count, "before": old, "after": new},
                    "part_buffering": {
                        "part_mib": 64,
                        "before": buffered,
                        "after": streamed,
                        "bytes_match": True,
                    },
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
