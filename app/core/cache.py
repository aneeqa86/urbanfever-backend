"""Tiny thread-safe in-memory TTL cache for expensive Earth Engine results."""
import functools
import threading
import time

from app.core.config import settings

_lock = threading.Lock()
_store: dict = {}
_MAX_ITEMS = 512


def ttl_cache(ttl: int | None = None):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__qualname__, args, tuple(sorted(kwargs.items())))
            now = time.time()
            with _lock:
                hit = _store.get(key)
                if hit and hit[0] > now:
                    return hit[1]
            value = fn(*args, **kwargs)
            with _lock:
                if len(_store) >= _MAX_ITEMS:
                    for k in sorted(_store, key=lambda k: _store[k][0])[: _MAX_ITEMS // 4]:
                        _store.pop(k, None)
                _store[key] = (now + (ttl or settings.CACHE_TTL_SECONDS), value)
            return value

        return wrapper

    return decorator
