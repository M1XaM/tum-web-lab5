import base64
import json
import os
import time
from typing import Any, Dict, Optional, Tuple


CACHE_TTL_SECONDS = 300
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")


def load_cache() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(CACHE_FILE):
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as cache_file:
            data = json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    temp_file = f"{CACHE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file)
    os.replace(temp_file, CACHE_FILE)


def make_cache_key(url: str, max_redirects: int) -> str:
    return f"{url}::redirects={max_redirects}"


def get_cached_response(url: str, max_redirects: int) -> Optional[Tuple[int, Dict[str, str], bytes, str]]:
    cache = load_cache()
    key = make_cache_key(url, max_redirects)
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None

    cached_at = entry.get("cached_at")
    if not isinstance(cached_at, (int, float)):
        return None

    if time.time() - float(cached_at) > CACHE_TTL_SECONDS:
        del cache[key]
        save_cache(cache)
        return None

    status = entry.get("status")
    headers = entry.get("headers")
    body_b64 = entry.get("body_b64")
    final_url = entry.get("final_url")

    if not isinstance(status, int):
        return None
    if not isinstance(headers, dict):
        return None
    if not isinstance(body_b64, str):
        return None
    if not isinstance(final_url, str):
        return None

    normalized_headers = {str(k): str(v) for k, v in headers.items()}

    try:
        body = base64.b64decode(body_b64.encode("ascii"), validate=True)
    except (ValueError, OSError):
        return None

    return status, normalized_headers, body, final_url


def store_cached_response(
    url: str,
    max_redirects: int,
    status: int,
    headers: Dict[str, str],
    body: bytes,
    final_url: str,
) -> None:
    if status >= 400:
        return

    cache = load_cache()
    key = make_cache_key(url, max_redirects)
    cache[key] = {
        "cached_at": time.time(),
        "status": status,
        "headers": headers,
        "body_b64": base64.b64encode(body).decode("ascii"),
        "final_url": final_url,
    }
    save_cache(cache)
