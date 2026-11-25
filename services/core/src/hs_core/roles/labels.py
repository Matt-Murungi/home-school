"""M05's `LabelDirectory` over the Appwrite users (labels are authoritative, ADR 0012).

Not wired by default: M05 still derives labels from `role_assignment`, which equals this while the
role service keeps both in step. Once the owner has verified `account`/`users` label behaviour on
a throwaway project (docs/ops/roles.md, "Unverified"), switch with one line in `main.create_app`:
`wire_permissions(..., labels=DirectoryLabels(user_directory))` (staging blocker, memory.md).
"""

from __future__ import annotations

from ..repository import RepositoryUnavailable
from .directory import DirectoryUnavailable, UserDirectory
from .model import clean_labels


class DirectoryLabels:
    def __init__(self, users: UserDirectory) -> None:
        self._users = users

    def labels_of(self, user_id: str) -> frozenset[str]:
        try:
            u = self._users.get(user_id)
        except DirectoryUnavailable:
            raise RepositoryUnavailable("label directory unavailable") from None
        return frozenset() if u is None else clean_labels(u.labels)
