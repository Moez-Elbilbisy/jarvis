"""
Jarvis Web Scraper -- BeautifulSoup4 static page extraction.

Complements pc_open_url (open in browser) and pc_web_search (find pages)
with structured data extraction: Jarvis can READ a page and hand the
model clean facts instead of a raw HTML blob.

  - web_scrape_page: fetch + parse, one call, four extraction modes
      article   -> readable body text (scripts/styles/nav/ads stripped)
      links     -> outbound links with optional domain/keyword filters
      table     -> HTML tables as structured JSON
      selector  -> arbitrary CSS selector -> text or attribute

Design constraints:
  - requests with a realistic browser User-Agent and a hard <=4s timeout
    (kept under Jarvis's 5-second reply ceiling even with parse time)
  - Honest errors: DNS failure, timeout, non-200, empty results are all
    reported exactly. Nothing is faked.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

_FETCH_TIMEOUT = 4.0          # hard cap; leaves ~1s for parse+reply
_MAX_HTML_BYTES = 3_000_000   # 3 MB -- skip giant/binary payloads
_MAX_TEXT_CHARS = 12_000      # keep the tool result LLM-friendly

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Tags removed before any extraction
_STRIP_TAGS = ("script", "style", "noscript", "template", "iframe", "svg",
               "nav", "footer", "header", "aside", "form", "button")


# ── Fetch helper ─────────────────────────────────────────────────

def _fetch(url: str) -> requests.Response:
    """GET with browser headers and a hard timeout. Raises on failure."""
    if not url.strip().lower().startswith(("http://", "https://")):
        url = "https://" + url.strip()
    resp = requests.get(
        url,
        headers=_HEADERS,
        timeout=_FETCH_TIMEOUT,
        allow_redirects=True,
        stream=True,
    )
    # stream=True lets us cap the body size before reading it all
    content = resp.raw.read(_MAX_HTML_BYTES + 1, decode_content=True)
    resp._content = content
    resp.status_code  # keep attribute access intact
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} {resp.reason} for {url}")
    return resp


# ── Extraction modes ─────────────────────────────────────────────

def _extract_article(soup: BeautifulSoup) -> str:
    """Readable body text: drop chrome, prefer <article>/<main>/OG fallback."""
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    # Prefer semantic containers, fall back to the whole body
    root = (soup.find("article") or soup.find("main")
            or soup.find("div", role="main") or soup.body or soup)

    # Drop obvious ad/share boilerplate inside the root
    for tag in root.find_all(class_=lambda c: c and isinstance(c, str) and (
            any(k in c.lower() for k in
                ("ad", "sponsor", "share", "social", "newsletter",
                 "related", "comment", "banner", "promo")))):
        tag.decompose()

    text = root.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 2]
    body = "\n".join(lines)

    if len(body) < 80:
        # Tiny/JS-walled page: try meta description so the model gets SOMETHING
        og = (soup.find("meta", property="og:description")
              or soup.find("meta", attrs={"name": "description"}))
        if og and og.get("content"):
            body = (f"[Page body is mostly JS-rendered or empty. Meta "
                    f"description: {og['content']}]")
        else:
            body = body or "[No readable static text found on this page.]"
    return body[:_MAX_TEXT_CHARS]


def _extract_links(soup: BeautifulSoup,
                   base_url: str,
                   domain: Optional[str] = None,
                   keyword: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, str]]:
    """Outbound (or domain-filtered) links with optional keyword filter."""
    from urllib.parse import urljoin
    site = urlparse(base_url).netloc.lower().removeprefix("www.")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:",
                                        "tel:", "data:")):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        host = parsed.netloc.lower().removeprefix("www.")
        if domain:
            d = domain.lower().lstrip("@").removeprefix("www.")
            if d not in host:
                continue
        elif host == site:
            continue  # default: outbound only
        text = " ".join(a.get_text(separator=" ", strip=True).split())[:120]
        key = abs_url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": abs_url.split("#")[0], "text": text})
        if len(out) >= max(1, min(int(limit or 50), 200)):
            break
    return out


def _extract_tables(soup: BeautifulSoup) -> List[List[List[str]]]:
    """All <table> elements as nested lists of cell strings."""
    tables = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = [" ".join(c.get_text(separator=" ", strip=True).split())
                     for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
        if len(tables) >= 10:  # sane cap
            break
    return tables


def _extract_selector(soup: BeautifulSoup, selector: str,
                      attribute: Optional[str] = None) -> List[Dict[str, str]]:
    """CSS select (soupsieve via bs4) -> text or attribute per match."""
    try:
        nodes = soup.select(selector)
    except Exception as e:  # noqa: BLE001 -- invalid selector syntax
        raise ValueError(f"Invalid CSS selector '{selector}': {e}") from e
    out = []
    for n in nodes[:100]:
        if attribute:
            val = n.get(attribute)
            if val is not None:
                out.append({"attribute": attribute, "value": str(val)})
        else:
            txt = " ".join(n.get_text(separator=" ", strip=True).split())
            if txt:
                out.append({"text": txt[:2000]})
    return out


# ── Main tool entry ──────────────────────────────────────────────

def scrape_page(url: str,
                mode: str = "article",
                selector: Optional[str] = None,
                attribute: Optional[str] = None,
                domain: Optional[str] = None,
                keyword: Optional[str] = None,
                limit: int = 50,
                **kwargs) -> Dict[str, Any]:
    """Fetch a URL and extract structured data. Honest errors on failure.

    mode: article | links | table | selector
    """
    mode = (mode or "article").strip().lower()
    if mode not in ("article", "links", "table", "selector"):
        return {"success": False,
                "error": (f"Unknown mode '{mode}'. Use article, links, "
                          "table, or selector."),
                "url": url}
    if mode == "selector" and not selector:
        return {"success": False,
                "error": "mode='selector' requires a 'selector' parameter.",
                "url": url}

    try:
        resp = _fetch(url)
    except requests.exceptions.Timeout:
        return {"success": False,
                "error": f"Request timed out after {_FETCH_TIMEOUT:.0f}s.",
                "url": url}
    except requests.exceptions.ConnectionError as e:
        return {"success": False,
                "error": f"Connection failed: {e}",
                "url": url}
    except requests.exceptions.MissingSchema:
        return {"success": False, "error": "Invalid URL.", "url": url}
    except Exception as e:  # noqa: BLE001 -- DNS, TLS, HTTP>=400, etc.
        return {"success": False, "error": str(e), "url": url}

    final_url = str(resp.url)
    try:
        html = resp.text
    except Exception as e:  # noqa: BLE001 -- decoding problems
        return {"success": False, "error": f"Could not decode page: {e}",
                "url": final_url}
    if not html.strip():
        return {"success": False, "error": "Page returned empty content.",
                "url": final_url}

    soup = BeautifulSoup(html, "html.parser")

    try:
        if mode == "article":
            data: Any = _extract_article(soup)
        elif mode == "links":
            data = _extract_links(soup, final_url, domain=domain,
                                  keyword=keyword, limit=limit)
            if not data:
                return {"success": False,
                        "error": ("No matching links found (filters: "
                                  f"domain={domain or 'any-outbound'}, "
                                  f"keyword={keyword or 'none'})."),
                        "url": final_url}
        elif mode == "table":
            data = _extract_tables(soup)
            if not data:
                return {"success": False,
                        "error": "No HTML tables found on this page.",
                        "url": final_url}
        else:  # selector
            data = _extract_selector(soup, selector, attribute)
            if not data:
                return {"success": False,
                        "error": (f"Selector '{selector}' matched nothing "
                                  "on the rendered-static HTML. If the "
                                  "content is JS-loaded, use "
                                  "web_browser_interact(get_rendered_html) "
                                  "instead."),
                        "url": final_url}
    except ValueError as e:
        return {"success": False, "error": str(e), "url": final_url}

    return {"success": True, "data": data, "url": final_url, "error": None}


# ── Gemini FunctionDeclaration ───────────────────────────────────

def get_declarations():
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name="web_scrape_page",
            description=(
                "Fetch a web page and extract structured data WITHOUT a "
                "browser: 'article' (clean readable text), 'links' (outbound "
                "links with filters), 'table' (HTML tables as JSON), or "
                "'selector' (CSS selector -> text/attribute). Static HTML "
                "only -- for JS-heavy pages use web_browser_interact."),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "The page URL to fetch."},
                    "mode": {
                        "type": "string",
                        "description": ("Extraction mode: 'article', "
                                        "'links', 'table', or 'selector'."),
                    },
                    "selector": {
                        "type": "string",
                        "description": ("CSS selector (selector mode), e.g. "
                                        "'h1', 'div.price', 'a.title'."),
                    },
                    "attribute": {
                        "type": "string",
                        "description": ("With selector mode: extract this "
                                        "attribute (e.g. 'href', 'src') "
                                        "instead of text."),
                    },
                    "domain": {
                        "type": "string",
                        "description": ("links mode: only links whose host "
                                        "contains this domain."),
                    },
                    "keyword": {
                        "type": "string",
                        "description": ("links mode: only links whose URL "
                                        "or text contains this keyword."),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max links to return (links mode, default 50).",
                    },
                },
                "required": ["url"],
            },
        ),
    ]


# ── Self-test ────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    r = scrape_page("https://example.com", mode="article")
    print(json.dumps(r, indent=2)[:600])
