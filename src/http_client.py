import socket
import ssl
from typing import Dict, Tuple
from urllib.parse import urlparse

from src.cache.store import get_cached_response, store_cached_response
from src.redirection.redirects import RedirectLimitReached, is_redirect_status, resolve_next_url


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

        if is_redirect_status(status_code):
            try:
                next_url = resolve_next_url(current_url, headers)
            except ValueError as exc:
                raise HTTPError(str(exc)) from exc

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
