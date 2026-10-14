import hashlib
import threading
import time
from collections import deque

from fastapi import Header, HTTPException, status

from fin_assistant.config import settings

_WINDOW_S = 60.0
_lock = threading.Lock()
_calls: dict[str, deque[float]] = {}


def _keys() -> set[str]:
    return {k.strip() for k in settings.api_keys.split(",") if k.strip()}


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def require_key(x_api_key: str | None = Header(default=None)) -> str:
    keys = _keys()
    if not keys:
        return "anonymous"
    if not x_api_key or x_api_key not in keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-API-Key")
    ident = key_id(x_api_key)
    now = time.monotonic()
    with _lock:
        window = _calls.setdefault(ident, deque())
        while window and now - window[0] > _WINDOW_S:
            window.popleft()
        if len(window) >= settings.rate_limit_per_minute:
            retry = int(_WINDOW_S - (now - window[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit {settings.rate_limit_per_minute}/min",
                headers={"Retry-After": str(retry)},
            )
        window.append(now)
    return ident
