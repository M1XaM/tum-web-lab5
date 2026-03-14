import html
import json
import re
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlparse


def get_content_category(headers: Dict[str, str]) -> str:
    content_type = headers.get("content-type", "").lower()
    if "application/json" in content_type or content_type.endswith("+json"):
        return "json"
    if "text/html" in content_type or "application/xhtml+xml" in content_type:
        return "html"
    return "other"


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


def format_response_body(body: bytes, headers: Dict[str, str]) -> str:
    text = decode_body(body, headers)
    content_category = get_content_category(headers)

    if content_category == "json":
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return text

    if content_category == "html":
        return text

    return text


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def extract_search_results(page_url: str, html_text: str, limit: int = 10) -> List[Tuple[str, str]]:
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
            query_values = parse_qs(parsed.query)
            if "uddg" in query_values and query_values["uddg"]:
                absolute = unquote(query_values["uddg"][0])
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
