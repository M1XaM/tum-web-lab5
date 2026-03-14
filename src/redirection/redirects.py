from typing import Dict
from urllib.parse import urljoin


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class RedirectLimitReached(Exception):
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


def is_redirect_status(status_code: int) -> bool:
    return status_code in REDIRECT_STATUS_CODES


def resolve_next_url(current_url: str, headers: Dict[str, str]) -> str:
    location = headers.get("location")
    if not location:
        raise ValueError("Redirect received without Location header")
    return urljoin(current_url, location)
