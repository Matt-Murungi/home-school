"""Storage objects behind purged rows (C03 #141): teacher documents, school reports, question
media. The purger deletes the file BEFORE the row (a crash in between leaves a row whose file is
gone, which the next run finishes; the other order would leave an orphaned personal document that
nothing points at). `sweep_orphans` is the safety net for files whose row is already gone.

`RetentionFileStore` is the interface; `FakeRetentionFileStore` is the test double;
`AppwriteRetentionFileStore` wraps the SDK Storage service (mock-tested only: the real calls are
owner-blocked, memory.md "Blocked on owner").
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..repository import NotFound, RepositoryUnavailable


@dataclass(frozen=True)
class FileInfo:
    id: str
    created_at: datetime


class RetentionFileStore(Protocol):
    def delete(self, bucket: str, file_id: str) -> bool:
        """Delete the file. True if it existed, False if it was already gone (idempotent)."""

    def list_files(self, bucket: str, after: str | None = None, limit: int = 100) -> list[FileInfo]:
        """Files in id order, strictly after `after` (cursor paging, at most 100)."""


class FakeRetentionFileStore:
    def __init__(self) -> None:
        self._f: dict[str, dict[str, datetime]] = {}
        self._lock = threading.Lock()
        self.deleted: list[tuple[str, str]] = []
        self.fail_on: Callable[[str, str], None] | None = None

    def put(self, bucket: str, file_id: str, created_at: datetime | None = None) -> None:
        with self._lock:
            self._f.setdefault(bucket, {})[file_id] = created_at or datetime(2020, 1, 1, tzinfo=UTC)

    def exists(self, bucket: str, file_id: str) -> bool:
        return file_id in self._f.get(bucket, {})

    def delete(self, bucket: str, file_id: str) -> bool:
        if self.fail_on:
            self.fail_on(bucket, file_id)
        with self._lock:
            had = file_id in self._f.get(bucket, {})
            self._f.get(bucket, {}).pop(file_id, None)
            self.deleted.append((bucket, file_id))
            return had

    def list_files(self, bucket: str, after: str | None = None, limit: int = 100) -> list[FileInfo]:
        with self._lock:
            ids = sorted(i for i in self._f.get(bucket, {}) if after is None or i > after)
            return [FileInfo(i, self._f[bucket][i]) for i in ids[: min(limit, 100)]]


class AppwriteRetentionFileStore:
    def __init__(self, client: Any, storage: Any | None = None) -> None:
        if storage is None:
            from appwrite.services.storage import Storage

            storage = Storage(client)
        self._s = storage

    def _wrap(self, fn, *a, **kw):
        from appwrite.exception import AppwriteException

        try:
            return fn(*a, **kw)
        except AppwriteException as e:
            if e.code == 404:
                raise NotFound(str(e.message)) from None
            raise RepositoryUnavailable(f"appwrite error {e.code}") from None
        except OSError as e:
            raise RepositoryUnavailable(type(e).__name__) from None

    def delete(self, bucket: str, file_id: str) -> bool:
        try:
            self._wrap(self._s.delete_file, bucket_id=bucket, file_id=file_id)
            return True
        except NotFound:
            return False

    def list_files(self, bucket: str, after: str | None = None, limit: int = 100) -> list[FileInfo]:
        from appwrite.query import Query

        q = [Query.order_asc("$id"), Query.limit(min(limit, 100))]
        if after:
            q.append(Query.cursor_after(after))
        res = self._wrap(self._s.list_files, bucket_id=bucket, queries=q)
        files = res["files"] if isinstance(res, dict) else res.files
        out = []
        for f in files:
            d = f if isinstance(f, dict) else f.to_dict()
            out.append(
                FileInfo(d["$id"], datetime.fromisoformat(d["$createdAt"].replace("Z", "+00:00")))
            )
        return out


def sweep_orphans(
    repo: Any,
    files: RetentionFileStore,
    policy: dict,
    now: datetime,
    *,
    enforce: bool,
    min_age: timedelta = timedelta(hours=24),
    max_delete: int = 500,
) -> dict[str, int]:
    """Safety net: delete Storage files that no row references any more (a crash between the file
    delete and the row delete leaves the OTHER kind of orphan: a row without a file, which is
    harmless). Only files older than `min_age` are considered: an upload whose row is not created
    yet must survive. Cursor-paged, at most 100 per call, capped per run. Dry run counts only."""
    from appwrite.query import Query

    out = {"scanned": 0, "orphans": 0, "deleted": 0}
    refs: dict[str, list[tuple[str, str]]] = {}
    for r in policy.values():
        for f in r.files:
            refs.setdefault(f.bucket, []).append((r.table, f.column))
    for bucket, columns in sorted(refs.items()):
        after = None
        while True:
            page = files.list_files(bucket, after, 100)
            for info in page:
                out["scanned"] += 1
                after = info.id
                if now - info.created_at < min_age:
                    continue
                used = any(
                    repo.list(table, [Query.equal(col, [info.id]), Query.limit(1)])
                    for table, col in columns
                )
                if used:
                    continue
                out["orphans"] += 1
                if enforce and out["deleted"] < max_delete:
                    files.delete(bucket, info.id)
                    out["deleted"] += 1
            if len(page) < 100:
                break
    return out
