"""Thin HTTP helpers shared by the skills, with a short-lived cache.

Within one conversation the agent often calls the same skill several times with different filters; the cache
means the feeds and APIs behind it are fetched once, not on every call.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

from . import config, runtime
from .security import SecurityError, validate_source_url

CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()
MAX_CACHE_ENTRIES = 128
MAX_CACHE_CHARS = 8_000_000
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


def _fetch(url: str, max_bytes: int) -> tuple[bytes, str, str]:
    if config.offline():
        raise SecurityError("Source network access is disabled in offline mode.")
    deadline = time.monotonic() + runtime.timeout(config.HTTP_TIMEOUT)
    def remaining():
        runtime.checkpoint()
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("Document download deadline reached.")
        return seconds
    with requests.Session() as session:
        # Credentials/proxies in the process environment must not be attached to public reports.
        session.trust_env = False
        for _ in range(4):
            runtime.checkpoint()
            validate_source_url(url)
            with session.get(url, headers=config.HTTP_HEADERS, timeout=remaining(),
                             stream=True, allow_redirects=False) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    url = urljoin(url, resp.headers.get("Location", ""))
                    continue
                resp.raise_for_status()
                if int(resp.headers.get("Content-Length", "0")) > max_bytes:
                    raise SecurityError("Document exceeds the download size limit.")
                chunks, size = [], 0
                for chunk in resp.iter_content(chunk_size=65536):
                    remaining()
                    size += len(chunk)
                    if size > max_bytes:
                        raise SecurityError("Document exceeds the download size limit.")
                    chunks.append(chunk)
                runtime.checkpoint()
                return b"".join(chunks), resp.headers.get("Content-Type", ""), resp.encoding or "utf-8"
    raise SecurityError("Too many document redirects.")


def get_text(url: str) -> str:
    runtime.checkpoint()
    if config.offline():
        raise SecurityError("Source network access is disabled in offline mode.")
    validate_source_url(url, resolve=False)
    with _lock:
        hit = _cache.get(url)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    body, _, encoding = _fetch(url, 4 * 1024 * 1024)
    value = body.decode(encoding, errors="replace")
    with _lock:
        while _cache and (len(_cache) >= MAX_CACHE_ENTRIES or sum(len(v[1]) for v in _cache.values()) + len(value) > MAX_CACHE_CHARS):
            del _cache[min(_cache, key=lambda key: _cache[key][0])]
        if len(value) <= MAX_CACHE_CHARS:
            _cache[url] = (time.time(), value)
    return value


def get_bytes(url: str) -> tuple[bytes, str]:
    """Raw body and content type, for binary documents such as PDF reports (not cached; fetched rarely)."""
    body, content_type, _ = _fetch(url, MAX_DOCUMENT_BYTES)
    return body, content_type


def get_json(url: str) -> dict:
    return json.loads(get_text(url))


def get_many(urls: list[str]) -> dict[str, str | Exception]:
    """Fetch several URLs in parallel; failures come back as the exception instead of raising."""
    def fetch(url):
        try:
            return get_text(url)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [runtime.submit(pool, fetch, url) for url in urls]
        return dict(zip(urls, [f.result() for f in futures]))


def clear_cache() -> None:
    with _lock:
        _cache.clear()
