#!/usr/bin/env python3
import argparse
import socket
import ssl
import sys
from urllib.parse import quote_plus

from src.http_client import HTTPError, make_request
from src.redirection.redirects import RedirectLimitReached
from src.search.parsing import decode_body, extract_search_results, format_response_body, get_content_category


ACCEPT_OPTIONS = {
    "json": "application/json, */*;q=0.8",
    "html": "text/html, application/xhtml+xml;q=0.9, */*;q=0.8",
    "both": "application/json, text/html;q=0.9, */*;q=0.8",
}


def is_content_type_acceptable(accept_mode: str, headers: dict[str, str]) -> bool:
    content_category = get_content_category(headers)
    if accept_mode == "both":
        return content_category in ("json", "html")
    return content_category == accept_mode


def command_fetch_url(
    url: str,
    redirect_count: int = 0,
    use_cache: bool = True,
    accept_mode: str = "both",
    accept_header: str = ACCEPT_OPTIONS["both"],
    raw_html: bool = False,
) -> int:
    try:
        status, headers, body, _, is_cached = make_request(
            url,
            max_redirects=redirect_count,
            use_cache=use_cache,
            accept_header=accept_header,
        )

        if not is_content_type_acceptable(accept_mode, headers):
            actual = headers.get("content-type", "<missing>")
            print(
                f"Error: content type mismatch (expected {accept_mode}, got {actual})",
                file=sys.stderr,
            )
            return 1

        text = format_response_body(body, headers, raw_html=raw_html)
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


def command_search(
    term: str,
    use_cache: bool = True,
    accept_mode: str = "html",
    accept_header: str = ACCEPT_OPTIONS["html"],
    access_index: int | None = None,
    raw_html: bool = False,
) -> int:
    query = quote_plus(term)
    search_url = f"https://lite.duckduckgo.com/lite/?q={query}"

    try:
        status, headers, body, final_url, _ = make_request(
            search_url,
            max_redirects=0,
            use_cache=use_cache,
            accept_header=accept_header,
        )
        if status >= 400:
            print(f"Search request failed with HTTP {status}", file=sys.stderr)
            return 1

        if not is_content_type_acceptable(accept_mode, headers):
            actual = headers.get("content-type", "<missing>")
            print(
                f"Error: content type mismatch (expected {accept_mode}, got {actual})",
                file=sys.stderr,
            )
            return 1

        page = decode_body(body, headers)
        results = extract_search_results(final_url, page, limit=10)

        if not results:
            print("No results found.")
            return 0

        if access_index is not None:
            if access_index > len(results):
                print(
                    f"Error: --access {access_index} is out of range for available results ({len(results)})",
                    file=sys.stderr,
                )
                return 1
            target_url = results[access_index - 1][1]
            return command_fetch_url(
                target_url,
                redirect_count=0,
                use_cache=use_cache,
                accept_mode=accept_mode,
                accept_header=accept_header,
                raw_html=raw_html,
            )

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="go2web",
        description="Minimal CLI web fetch/search tool.",
    )
    command_group = parser.add_mutually_exclusive_group(required=True)
    command_group.add_argument("-u", "--url", help="Fetch and print content for URL")
    command_group.add_argument("-s", "--search", nargs="+", help="Search and print top results")

    parser.add_argument(
        "--redirect-count",
        type=int,
        default=0,
        help="Follow up to N redirects (only for -u)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Always fetch from source and skip cache",
    )
    parser.add_argument(
        "--accept",
        choices=["json", "html", "both"],
        default="both",
        help="Preferred response content type for requests",
    )
    parser.add_argument(
        "--access",
        type=int,
        help="With -s, fetch the N-th search result (1-10)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="For HTML responses, print raw HTML instead of human-readable text",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])

    if args.redirect_count < 0:
        print("Error: --redirect-count must be a non-negative integer", file=sys.stderr)
        return 1

    use_cache = not args.no_cache
    accept_header = ACCEPT_OPTIONS[args.accept]

    if args.url:
        if args.access is not None:
            print("Error: --access can only be used with -s", file=sys.stderr)
            return 1
        return command_fetch_url(
            args.url,
            redirect_count=args.redirect_count,
            use_cache=use_cache,
            accept_mode=args.accept,
            accept_header=accept_header,
            raw_html=args.raw,
        )

    if args.redirect_count != 0:
        print("Error: --redirect-count can only be used with -u", file=sys.stderr)
        return 1

    if args.access is not None and not (1 <= args.access <= 10):
        print("Error: --access must be an integer from 1 to 10", file=sys.stderr)
        return 1

    term = " ".join(args.search).strip()
    if not term:
        print("Error: empty search term", file=sys.stderr)
        return 1
    return command_search(
        term,
        use_cache=use_cache,
        accept_mode=args.accept,
        accept_header=accept_header,
        access_index=args.access,
        raw_html=args.raw,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
