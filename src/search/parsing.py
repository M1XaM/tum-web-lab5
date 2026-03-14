import html
import json
import re
from html.parser import HTMLParser
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlparse


def get_content_category(headers: Dict[str, str]) -> str:
    content_type = headers.get("content-type", "").lower()
    if "application/json" in content_type or content_type.endswith("+json"):
        return "json"
    if "text/html" in content_type or "application/xhtml+xml" in content_type:
        return "html"
    return "other"


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0
        self.block_tags = {
            "p",
            "div",
            "br",
            "li",
            "ul",
            "ol",
            "section",
            "article",
            "header",
            "footer",
            "main",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
            "table",
        }

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if self.skip_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            if self.skip_depth > 0:
                self.skip_depth -= 1
            return
        if self.skip_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        if data and data.strip():
            self.parts.append(data)


def html_to_readable_text(html_text: str) -> str:
    extractor = HTMLTextExtractor()
    extractor.feed(html_text)
    extractor.close()

    text = "".join(extractor.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


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


def format_response_body(body: bytes, headers: Dict[str, str], raw_html: bool = False) -> str:
    text = decode_body(body, headers)
    content_category = get_content_category(headers)

    if content_category == "json":
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return text

    if content_category == "html":
        if raw_html:
            return text
        return html_to_readable_text(text)

    if content_category == "other":
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
