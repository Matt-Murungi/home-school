"""File permission store behind `PermissionService` (SEC-5, ADR 0012: files get the same list as
their row). `FakeFileStore` (permissions.fake) is the test double; `AppwriteFileStore` wraps the
SDK Storage service with the API key. Only mock-tested here: the real call shapes are read from the
installed SDK, not run against a project (owner-blocked, memory.md)."""

from __future__ import annotations

from typing import Any, Protocol

from .repo_errors import Conflict, NotFound, RepositoryUnavailable


class FileStore(Protocol):
    def exists(self, bucket: str, file_id: str) -> bool: ...
    def get_permissions(self, bucket: str, file_id: str) -> list[str]: ...
    def update_permissions(self, bucket: str, file_id: str, permissions: list[str]) -> None: ...


def _plain(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


class AppwriteFileStore:
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
            if e.code == 409:
                raise Conflict(str(e.message)) from None
            raise RepositoryUnavailable(f"appwrite error {e.code}") from None
        except OSError as e:
            raise RepositoryUnavailable(type(e).__name__) from None

    def exists(self, bucket: str, file_id: str) -> bool:
        try:
            self._wrap(self._s.get_file, bucket_id=bucket, file_id=file_id)
            return True
        except NotFound:
            return False

    def get_permissions(self, bucket: str, file_id: str) -> list[str]:
        f = _plain(self._wrap(self._s.get_file, bucket_id=bucket, file_id=file_id))
        return list(f.get("$permissions", []))

    def update_permissions(self, bucket: str, file_id: str, permissions: list[str]) -> None:
        self._wrap(self._s.update_file, bucket_id=bucket, file_id=file_id, permissions=permissions)
