from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.thumbnail_cache import ThumbnailCache, thumbnail_cache_key


def test_miss_then_hit_and_versioned_key():
    cache = ThumbnailCache(10)
    key = thumbnail_cache_key("a" * 64)
    assert key == f"{'a' * 64}:200x200:webp:v1"
    assert cache.get(key) is None
    cache.put(key, b"webp")
    assert cache.get(key) == b"webp"
    assert cache.stats() == {"hits": 1, "misses": 1, "size_bytes": 4, "entries": 1}
    assert cache.get(key.replace(":v1", ":v2")) is None


def test_evict_least_recently_used_and_account_for_replacement():
    cache = ThumbnailCache(6)
    cache.put("a", b"aaa")
    cache.put("b", b"bbb")
    assert cache.get("a") == b"aaa"
    cache.put("c", b"ccc")
    assert cache.get("b") is None
    cache.put("a", b"a")
    assert cache.stats()["size_bytes"] == 4
    cache.put("d", b"dd")
    assert cache.stats()["size_bytes"] == 6
    assert cache.stats()["entries"] == 3


def test_oversized_empty_disabled_and_invalid_payloads():
    cache = ThumbnailCache(2)
    cache.put("a", b"ok")
    cache.put("large", b"big")
    cache.put("empty", b"")
    assert cache.stats()["entries"] == 1
    assert cache.get("a") == b"ok"
    disabled = ThumbnailCache(0)
    disabled.put("a", b"x")
    assert disabled.stats()["entries"] == 0
    with pytest.raises(ValueError):
        ThumbnailCache(-1)
    with pytest.raises(TypeError):
        cache.put("mutable", bytearray(b"x"))


def test_concurrent_access_keeps_budget_and_counters_consistent():
    cache = ThumbnailCache(32)
    def use_cache(index):
        key = str(index)
        cache.put(key, b"x" * 8)
        cache.get(key)
        cache.stats()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(use_cache, range(200)))
    stats = cache.stats()
    assert stats["hits"] + stats["misses"] == 200
    assert stats["size_bytes"] <= 32
    assert stats["size_bytes"] == stats["entries"] * 8
