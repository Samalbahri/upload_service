from collections import OrderedDict
from threading import Lock

MAX_CACHE_BYTES = 16 * 1024 * 1024


def thumbnail_cache_key(sha256: str) -> str:
    return f"{sha256}:200x200:webp:v1"


class ThumbnailCache:
    """Process-local LRU. Budget counts payload bytes, not Python overhead."""

    def __init__(self, max_bytes: int = MAX_CACHE_BYTES):
        if max_bytes < 0:
            raise ValueError("Cache size must be nonnegative")
        self.max_bytes = max_bytes
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._size_bytes = 0
        self._hits = self._misses = 0
        self._lock = Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            data = self._items.get(key)
            if data is None:
                self._misses += 1
            else:
                self._hits += 1
                self._items.move_to_end(key)
            return data

    def put(self, key: str, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("Cache payload must be bytes")
        if not data or len(data) > self.max_bytes:
            return
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._size_bytes -= len(previous)
            while self._size_bytes + len(data) > self.max_bytes:
                _, evicted = self._items.popitem(last=False)
                self._size_bytes -= len(evicted)
            self._items[key] = data
            self._size_bytes += len(data)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(hits=self._hits, misses=self._misses,
                        size_bytes=self._size_bytes, entries=len(self._items))


thumbnail_cache = ThumbnailCache()
