"""A small thread-safe LRU for content of published/retired versions (immutable, so never stale)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Hashable
from typing import Any


class LruCache:
    def __init__(self, max_entries: int) -> None:
        self._max = max(1, max_entries)
        self._d: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            if key not in self._d:
                return None
            self._d.move_to_end(key)
            return self._d[key]

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)
