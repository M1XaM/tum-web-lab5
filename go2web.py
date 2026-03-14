#!/usr/bin/env python3
import html
import re
import socket
import ssl
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse


HELP_TEXT = """go2web -u <URL>         # make an HTTP request to the specified URL and print the response
go2web -s <search-term> # make an HTTP request to search the term using your favorite search engine and print top 10 results
go2web -h               # show this help"""


class HTTPError(Exception):
    pass


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


def make_request(url: str) -> Tuple[int, Dict[str, str], bytes, str]:
    parsed = urlparse(url)
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

    return status_code, headers, body, url


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


def command_fetch_url(url: str) -> int:
    try:
        status, headers, body, _ = make_request(url)
        text = decode_body(body, headers)
        print(f"HTTP {status}\n")
        print(text)
        return 0
    except (socket.error, ssl.SSLError, HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def command_search(term: str) -> int:
    query = quote_plus(term)
    search_url = f"https://lite.duckduckgo.com/lite/?q={query}"

    try:
        status, headers, body, final_url = make_request(search_url)
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
    except (socket.error, ssl.SSLError, HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main(argv: List[str]) -> int:
    args = argv[1:]

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
        j = 0
        while j < len(args):
            token = args[j]
            if token == "-u":
                j += 2
                continue

            print("Error: unknown arguments", file=sys.stderr)
            print(HELP_TEXT)
            return 1

        return command_fetch_url(url)

    if "-s" in args:
        i = args.index("-s")
        if i + 1 >= len(args):
            print("Error: missing search term after -s", file=sys.stderr)
            print(HELP_TEXT)
            return 1
        term = " ".join(args[i + 1:]).strip()
        if not term:
            print("Error: empty search term", file=sys.stderr)
            return 1
        return command_search(term)

    print("Error: unknown arguments", file=sys.stderr)
    print(HELP_TEXT)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
