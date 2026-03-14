# go2web

Minimal CLI web fetch/search tool with caching, content negotiation, and optional result access.

## Requirements

- Python 3.10+
- Network access

## Quick Start

```bash
chmod +x go2web.py
./go2web.py -h

# Output:
Minimal CLI web fetch/search tool.

options:
  -h, --help            show this help message and exit
  -u URL, --url URL     Fetch and print content for URL
  -s SEARCH [SEARCH ...], --search SEARCH [SEARCH ...]
                        Search and print top results
  --redirect-count REDIRECT_COUNT
                        Follow up to N redirects (only for -u)
  --no-cache            Always fetch from source and skip cache
  --accept {json,html,both}
                        Preferred response content type for requests
  --access ACCESS       With -s, fetch the N-th search result (1-10)
  --raw                 For HTML responses, print raw HTML instead of human-readable text
```

## Use Cases

### 1) Fetch a URL

```bash
./go2web.py -u https://example.com
```

![Fetch URL demo](docs/gifs/fetch-url.gif)

---

### 2) Search and list top results

```bash
./go2web.py -s "python cli"
# or 
./go2web.py -s python cli
```

![Search list demo](docs/gifs/search-list.gif)

---

### 3) Follow redirects for URL fetch

```bash
./go2web.py -u http://example.com --redirect-count 3
```

Notes:
- `--redirect-count` works only with `-u`.
- If the limit is reached, the tool prints redirect debug info and exits with non-zero code.

![Redirect count demo](docs/gifs/redirect-count.gif)

---

### 4) Use cache (default)

Run the same request twice; the second response is served from cache and prints `Cached response` before and after content.

```bash
./go2web.py -u https://example.com
./go2web.py -u https://example.com
```

![Cache hit demo](docs/gifs/cache-hit.gif)

---

### 5) Bypass cache

```bash
./go2web.py --no-cache -u https://example.com
./go2web.py --no-cache -s "python"
```

![No cache demo](docs/gifs/no-cache.gif)

---

### 6) Content negotiation with strict type enforcement

#### JSON only

```bash
./go2web.py --accept json -u https://httpbin.org/json
```

#### HTML only

```bash
./go2web.py --accept html -u https://example.com
```

#### Accept both

```bash
./go2web.py --accept both -u https://example.com
```

Notes:
- `--accept json` requires JSON response content type.
- `--accept html` requires HTML/XHTML response content type.
- `--accept both` accepts either JSON or HTML/XHTML.
- On mismatch, command fails with: `Error: content type mismatch (...)`.

![Accept negotiation demo](docs/gifs/accept-negotiation.gif)

---

### 7) Access N-th search result directly (1 to 10)

Instead of listing only, this fetches the selected result URL.

```bash
./go2web.py -s "python" --access 1
./go2web.py -s "python" --access 3 --accept html
```

Notes:
- `--access` works only with `-s`.
- Valid range is `1..10`.
- If selected index is larger than available results, command fails with an error.

![Access result demo](docs/gifs/access-result.gif)

---

## Error Cases

- `--access` with `-u` → invalid usage error.
- `--redirect-count` with `-s` → invalid usage error.
- Negative `--redirect-count` → validation error.
- Out-of-range `--access` → validation error.
- Unsupported URL scheme (non-http/https) → request error.

![Error cases demo](docs/gifs/error-cases.gif)
