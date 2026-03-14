#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import socket
import ssl
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse


HELP_TEXT = """go2web -u <URL>         # make an HTTP request to the specified URL and print the response
go2web -s <search-term> # make an HTTP request to search the term using your favorite search engine and print top 10 results
go2web -u <URL> --redirect-count <N> # optional with -u only: follow up to N redirects (default: 0)
go2web --no-cache ...   # optional: force network fetch and skip cache usage for this request
go2web -h               # show this help"""


CACHE_TTL_SECONDS = 300
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, ".go2web_http_cache.json")


class HTTPError(Exception):
    pass


class RedirectLimitReached(HTTPError):
    def __init__(
        self,
        max_redirects: int,
        last_url: str,
        status_code: int,
        headers: Dict[str, str],
        body: bytes,
        raw_response: bytes,
        next_url: str,
    ):
        super().__init__(f"Redirect limit reached ({max_redirects})")
        self.last_url = last_url
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.raw_response = raw_response
        self.next_url = next_url


def load_cache() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(CACHE_FILE):
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    directory = os.path.dirname(CACHE_FILE)
    os.makedirs(directory, exist_ok=True)

    temp_file = f"{CACHE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as fp:
        json.dump(cache, fp)
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


def parse_headers(raw_headers: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for line in raw_headers.split("\r\n"):
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def decode_chunked(data: bytes) -> bytes:
    pos = 0
    result = bytearray()
    while True:
        line_end = data.find(b"\r\n", pos)
        if line_end == -1:
            break
        size_line = data[pos:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError:
            break
        pos = line_end + 2
        if size == 0:
            break
        if pos + size > len(data):
            break
        result.extend(data[pos:pos + size])
        pos += size + 2
    return bytes(result)


def open_socket(host: str, port: int, use_tls: bool) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=10)
    if use_tls:
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=host)
    return sock


def make_request(url: str, max_redirects: int = 0, use_cache: bool = True) -> Tuple[int, Dict[str, str], bytes, str, bool]:
    if use_cache:
        cached_response = get_cached_response(url, max_redirects)
        if cached_response is not None:
            status, headers, body, final_url = cached_response
            return status, headers, body, final_url, True

    current_url = url
    redirects_followed = 0

    for _ in range(max_redirects + 1):
        parsed = urlparse(current_url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise HTTPError("Only http:// and https:// URLs are supported")

        host = parsed.hostname
        if not host:
            raise HTTPError("Invalid URL: missing host")

        use_tls = scheme == "https"
        port = parsed.port or (443 if use_tls else 80)

        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        host_header = host
        if parsed.port and ((use_tls and parsed.port != 443) or (not use_tls and parsed.port != 80)):
            host_header = f"{host}:{parsed.port}"

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "User-Agent: go2web/1.0\r\n"
            "Accept: */*\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8")

        with open_socket(host, port, use_tls) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                block = sock.recv(4096)
                if not block:
                    break
                chunks.append(block)
            raw_response = b"".join(chunks)

        sep = b"\r\n\r\n"
        idx = raw_response.find(sep)
        if idx == -1:
            raise HTTPError("Malformed HTTP response")

        head = raw_response[:idx].decode("iso-8859-1", errors="replace")
        body = raw_response[idx + len(sep):]

        header_lines = head.split("\r\n")
        status_line = header_lines[0] if header_lines else ""
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise HTTPError("Malformed HTTP status line")

        status_code = int(parts[1])
        headers = parse_headers("\r\n".join(header_lines[1:]))

        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = decode_chunked(body)

        if status_code in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if not location:
                raise HTTPError("Redirect received without Location header")
            next_url = urljoin(current_url, location)
            if redirects_followed >= max_redirects:
                raise RedirectLimitReached(
                    max_redirects=max_redirects,
                    last_url=current_url,
                    status_code=status_code,
                    headers=headers,
                    body=body,
                    raw_response=raw_response,
                    next_url=next_url,
                )
            redirects_followed += 1
            current_url = next_url
            continue

        if use_cache:
            store_cached_response(
                url=url,
                max_redirects=max_redirects,
                status=status_code,
                headers=headers,
                body=body,
                final_url=current_url,
            )
        return status_code, headers, body, current_url, False

    raise HTTPError("Too many redirects")


def decode_body(body: bytes, headers: Dict[str, str]) -> str:
    content_type = headers.get("content-type", "")
    charset = "utf-8"
    match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
    if match:
        charset = match.group(1).strip('"').lower()
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def extract_search_results(page_url: str, html_text: str, limit: int = 10) -> List[Tuple[str, str]]:
    # Works for DuckDuckGo Lite HTML results, with graceful fallback for generic pages.
    anchors = re.findall(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, flags=re.IGNORECASE | re.DOTALL)

    results: List[Tuple[str, str]] = []
    seen = set()

    for href, raw_title in anchors:
        title = re.sub(r"\s+", " ", strip_tags(raw_title))
        if not title:
            continue

        absolute = urljoin(page_url, html.unescape(href))
        parsed = urlparse(absolute)

        if not parsed.scheme.startswith("http"):
            continue

        host = (parsed.netloc or "").lower()

        if "duckduckgo.com" in host and parsed.path.startswith("/l/"):
            q = parse_qs(parsed.query)
            if "uddg" in q and q["uddg"]:
                absolute = unquote(q["uddg"][0])
                parsed = urlparse(absolute)
                host = (parsed.netloc or "").lower()

        if not host:
            continue

        lower_url = absolute.lower()
        if any(skip in lower_url for skip in ["javascript:", "#", "duckduckgo.com/about", "duckduckgo.com/feedback"]):
            continue

        key = (title, absolute)
        if key in seen:
            continue
        seen.add(key)
        results.append((title, absolute))

        if len(results) >= limit:
            break

    return results


def command_fetch_url(url: str, redirect_count: int = 0, use_cache: bool = True) -> int:
    try:
        status, headers, body, _, is_cached = make_request(url, max_redirects=redirect_count, use_cache=use_cache)
        text = decode_body(body, headers)
        print(f"HTTP {status}\n")
        if is_cached:
            print("Cached response")
        print(text)
        if is_cached:
            print("Cached response")
        return 0
    except RedirectLimitReached as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Last parsed site: {exc.last_url}", file=sys.stderr)
        print(f"Next URL to parse: {exc.next_url}", file=sys.stderr)
        print("Full last HTTP response:\n")
        print(exc.raw_response.decode("iso-8859-1", errors="replace"))
        return 1
    except (socket.error, ssl.SSLError, HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def command_search(term: str, use_cache: bool = True) -> int:
    query = quote_plus(term)
    search_url = f"https://lite.duckduckgo.com/lite/?q={query}"

    try:
        status, headers, body, final_url, _ = make_request(search_url, max_redirects=0, use_cache=use_cache)
        if status >= 400:
            print(f"Search request failed with HTTP {status}", file=sys.stderr)
            return 1

        page = decode_body(body, headers)
        results = extract_search_results(final_url, page, limit=10)

        if not results:
            print("No results found.")
            return 0

        for idx, (title, url) in enumerate(results, start=1):
            print(f"{idx}. {title}")
            print(f"   {url}")
        return 0
    except RedirectLimitReached as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Last parsed site: {exc.last_url}", file=sys.stderr)
        return 1
    except (socket.error, ssl.SSLError, HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main(argv: List[str]) -> int:
    args = argv[1:]

    no_cache = False
    if "--no-cache" in args:
        no_cache = True
        args = [token for token in args if token != "--no-cache"]

    if not args or "-h" in args:
        print(HELP_TEXT)
        return 0

    if "-u" in args:
        i = args.index("-u")
        if i + 1 >= len(args):
            print("Error: missing URL after -u", file=sys.stderr)
            print(HELP_TEXT)
            return 1
        url = args[i + 1]

        redirect_count = 0
        j = 0
        while j < len(args):
            token = args[j]
            if token == "--redirect-count":
                if j + 1 >= len(args):
                    print("Error: missing number after --redirect-count", file=sys.stderr)
                    print(HELP_TEXT)
                    return 1
                value = args[j + 1]
                if not value.isdigit():
                    print("Error: --redirect-count must be a non-negative integer", file=sys.stderr)
                    return 1
                redirect_count = int(value)
                j += 2
                continue

            if token == "-u":
                j += 2
                continue

            print("Error: unknown arguments", file=sys.stderr)
            print(HELP_TEXT)
            return 1

        return command_fetch_url(url, redirect_count=redirect_count, use_cache=not no_cache)

    if "-s" in args:
        if "--redirect-count" in args:
            print("Error: --redirect-count can only be used with -u", file=sys.stderr)
            return 1

        i = args.index("-s")
        if i + 1 >= len(args):
            print("Error: missing search term after -s", file=sys.stderr)
            print(HELP_TEXT)
            return 1
        term = " ".join(args[i + 1:]).strip()
        if not term:
            print("Error: empty search term", file=sys.stderr)
            return 1
        return command_search(term, use_cache=not no_cache)

    print("Error: unknown arguments", file=sys.stderr)
    print(HELP_TEXT)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
