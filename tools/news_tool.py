"""
news_tool.py - Production Real-Time News & Media Retrieval Client for AI Agents.

Supports:
- Google News RSS Search (Hebrew hl=he&gl=IL&ceid=IL:he and Global English hl=en-US&gl=US&ceid=US:en)
- Israeli Hebrew News Portals RSS (Ynet, Haaretz, Israel Hayom, Maariv, Walla, Globes,
  TheMarker, Kan 11, C14, Arutz 7 / INN, Bhol, Mako / N12) with explicit UTF-8 byte decoding
- Telegram Public Channel Web Preview & Search (https://t.me/s/{channel}?q={query})
- GDELT Project 2.0 DOC API (global & multilingual news monitoring with 5s rate-limit handling)

Provides:
- Standardized NewsItem dataclass & dictionary contract
- Unified `search_news` dispatcher
- CLI and hardened stdio Model Context Protocol (MCP) server support
"""

import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (AgentDataSources-News/1.0)"
)

# Verified working Israeli & Global RSS Feeds
ISRAELI_RSS_FEEDS: Dict[str, str] = {
    "ynet": "https://www.ynet.co.il/Integration/StoryRss1854.xml",
    "ynet_main": "https://www.ynet.co.il/Integration/StoryRss2.xml",
    "haaretz": "https://www.haaretz.co.il/srv/rss---feedly",
    "israelhayom": "https://www.israelhayom.co.il/rss.xml",
    "maariv": "https://www.maariv.co.il/Rss/RssFeedsMivzakiChadashot",
    "walla": "https://rss.walla.co.il/feed/22",
    "globes": "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2",
    "globes_en": "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=1725",
    "themarker": "https://news.google.com/rss/search?q=site:themarker.com&hl=he&gl=IL&ceid=IL:he",
    "kan": "https://news.google.com/rss/search?q=site:kan.org.il/content/kan-news&hl=he&gl=IL&ceid=IL:he",
    "c14": "https://www.c14.co.il/feed/",
    "inn": "https://www.inn.co.il/Rss.aspx",
    "arutz7": "https://www.inn.co.il/Rss.aspx",
    "bhol": "https://www.bhol.co.il/rss/index.xml",
    "mako": "https://rcs.mako.co.il/rss/news-military.xml",
    "n12": "https://rcs.mako.co.il/rss/news-military.xml",
    "bbc": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "nyt": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "hn": "https://hnrss.org/newest",
}

_LAST_GDELT_CALL_TS: float = 0.0
_GDELT_TS_FILE = "/tmp/.agent_gdelt_last_call_ts"


@dataclass
class NewsItem:
    """Standardized representation of a news article or breaking channel item."""
    title: str
    source: str
    url: str
    published_at: str
    summary: str = ""
    language: str = "he"
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get_resilient_session(retries: int = 3, backoff: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return session


_SESSION: Optional[requests.Session] = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _get_resilient_session()
    return _SESSION


def safe_int(val: Any, default: int) -> int:
    """Safely cast value to integer, falling back to default if invalid."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _clean_html(raw: Optional[str]) -> str:
    """Strip HTML tags and unescape HTML entities."""
    if not raw:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _decode_xml_bytes(raw_bytes: bytes) -> str:
    """
    Explicitly decode RSS XML bytes.
    Handles Mako/N12 feeds that omit charset in HTTP Content-Type header (preventing
    requests from defaulting to ISO-8859-1) as well as legacy windows-1255 XML declarations.
    """
    head_ascii = raw_bytes[:250].decode("ascii", errors="ignore").lower()
    if "windows-1255" in head_ascii or "cp1255" in head_ascii:
        text = raw_bytes.decode("windows-1255", errors="replace")
        text = re.sub(r'encoding=["\']windows-1255["\']', 'encoding="utf-8"', text, flags=re.IGNORECASE)
        return text
    return raw_bytes.decode("utf-8", errors="replace")


# ==============================================================================
# 1. Google News RSS Search (Global & Hebrew IL:he)
# ==============================================================================
def search_google_news_rss(
    query: str,
    lang: str = "auto",
    country: str = "IL",
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Search Google News RSS feed with automatic Hebrew/English locale configuration.

    Args:
        query: Search keywords, phrase, or `site:domain.co.il` operator.
        lang: 'he', 'en', or 'auto' (detects Hebrew characters in query).
        country: Country code ('IL' or 'US').
        limit: Max articles to return (default 10).
    """
    limit = max(0, limit)
    if limit == 0 or not query.strip():
        return []

    is_heb = bool(re.search(r"[\u0590-\u05FF]", query)) if lang == "auto" else (lang.lower() == "he")
    if is_heb:
        hl, gl, ceid, resolved_lang = "he", "IL", "IL:he", "he"
    else:
        c_up = (country or "US").upper()
        if c_up == "IL":
            hl, gl, ceid, resolved_lang = "en-IL", "IL", "IL:en", "en"
        else:
            hl, gl, ceid, resolved_lang = "en-US", "US", "US:en", "en"

    url = "https://news.google.com/rss/search"
    params = {"q": query.strip(), "hl": hl, "gl": gl, "ceid": ceid}

    try:
        resp = _session().get(url, params=params, timeout=12)
        resp.raise_for_status()
        xml_text = _decode_xml_bytes(resp.content)
        root = ET.fromstring(xml_text)
    except Exception as e:
        return [{"error": f"Google News RSS request failed: {e}", "source": "google_news"}]

    items = root.findall(".//item")
    results: List[Dict[str, Any]] = []

    for it in items[:limit]:
        title = _clean_html(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        pub_date = (it.findtext("pubDate") or "").strip()
        desc = _clean_html(it.findtext("description"))
        source_el = it.find("source")
        publisher = source_el.text.strip() if (source_el is not None and source_el.text) else "Google News"
        publisher_url = source_el.attrib.get("url", "") if source_el is not None else ""

        news_item = NewsItem(
            title=title,
            source=publisher,
            url=link,
            published_at=pub_date,
            summary=desc,
            language=resolved_lang,
            extra={"feed": "google_news", "publisher_url": publisher_url}
        )
        results.append(news_item.to_dict())

    return results


# ==============================================================================
# 2. Israeli Hebrew News Portals RSS (Ynet, Haaretz, Mako/N12, Globes, C14, etc.)
# ==============================================================================
def fetch_israeli_rss(
    source: str = "ynet",
    query: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch live articles from major Israeli news RSS feeds with explicit UTF-8 decoding.

    Args:
        source: Feed key in ISRAELI_RSS_FEEDS (e.g. 'ynet', 'haaretz', 'israelhayom',
                'maariv', 'walla', 'globes', 'themarker', 'kan', 'c14', 'inn', 'bhol', 'mako').
        query: Optional keyword filter applied to title + summary.
        limit: Max items to return (default 10).
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    src_key = source.lower().strip()
    feed_url = ISRAELI_RSS_FEEDS.get(src_key)
    if not feed_url:
        return [{
            "error": f"Unknown RSS source '{source}'. Valid sources: {', '.join(sorted(ISRAELI_RSS_FEEDS.keys()))}",
            "source": source
        }]

    try:
        resp = _session().get(feed_url, timeout=12)
        resp.raise_for_status()
        xml_text = _decode_xml_bytes(resp.content)
        root = ET.fromstring(xml_text)
    except Exception as e:
        return [{"error": f"RSS fetch failed for '{source}': {e}", "source": src_key}]

    items = root.findall(".//item")
    results: List[Dict[str, Any]] = []
    q_lower = query.strip().lower() if query and query.strip() else None

    for it in items:
        if len(results) >= limit:
            break
        title = _clean_html(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        pub_date = (it.findtext("pubDate") or "").strip()
        desc = _clean_html(it.findtext("description"))

        if q_lower and q_lower not in (title + " " + desc).lower():
            continue

        lang = "en" if src_key in ("bbc", "nyt", "hn", "globes_en") else "he"
        news_item = NewsItem(
            title=title,
            source=src_key,
            url=link,
            published_at=pub_date,
            summary=desc,
            language=lang,
            extra={"feed_url": feed_url}
        )
        results.append(news_item.to_dict())

    return results


# ==============================================================================
# 3. Telegram Public Channel Web Preview & Search (t.me/s/{channel})
# ==============================================================================
def fetch_telegram_channel(
    channel: str = "amitsegal",
    query: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch real-time posts from a public Telegram channel web preview (`https://t.me/s/{channel}`).
    Requires zero API keys or Telegram authentication.

    Args:
        channel: Public Telegram handle (e.g. 'amitsegal', 'abualiexpress', 'israel_9', 'N12chat').
        query: Optional search query (`?q=...` server-side & client-side filter).
        limit: Max messages to return (newest first).
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    clean_chan = channel.strip().lstrip("@")
    if "t.me/" in clean_chan:
        clean_chan = clean_chan.split("t.me/")[-1].lstrip("s/").split("/")[0].split("?")[0]

    url = f"https://t.me/s/{clean_chan}"
    params: Dict[str, Any] = {}
    if query and query.strip():
        params["q"] = query.strip()

    try:
        resp = _session().get(url, params=params or None, timeout=12)
        resp.raise_for_status()
        page_html = resp.content.decode("utf-8", errors="replace")
    except Exception as e:
        return [{"error": f"Telegram web preview request failed for @{clean_chan}: {e}", "source": f"telegram:{clean_chan}"}]

    # Split into message widget blocks
    blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', page_html)
    parsed_posts: List[Dict[str, Any]] = []

    for block in blocks:
        post_match = re.search(r'data-post="([^"]+)"', block)
        if not post_match:
            continue
        post_id = post_match.group(1)
        text_match = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            block,
            re.DOTALL
        )
        raw_text = text_match.group(1) if text_match else ""
        clean_text = _clean_html(raw_text)
        if not clean_text:
            continue

        time_match = re.search(r'<time[^>]+datetime="([^"]+)"', block)
        pub_time = time_match.group(1) if time_match else ""
        views_match = re.search(r'<span class="tgme_widget_message_views">([^<]+)</span>', block)
        views = views_match.group(1).strip() if views_match else None

        title_line = clean_text[:90] + ("..." if len(clean_text) > 90 else "")
        lang = "he" if re.search(r"[\u0590-\u05FF]", clean_text) else "en"

        item = NewsItem(
            title=title_line,
            source=f"telegram:@{clean_chan}",
            url=f"https://t.me/{post_id}",
            published_at=pub_time,
            summary=clean_text,
            language=lang,
            extra={"post_id": post_id, "views": views}
        )
        parsed_posts.append(item.to_dict())

    # Newest messages are at the bottom of t.me/s/{channel}
    parsed_posts.reverse()

    if query and query.strip():
        q_lower = query.strip().lower()
        parsed_posts = [
            p for p in parsed_posts
            if q_lower in (p.get("summary", "") + " " + p.get("title", "")).lower()
        ]

    return parsed_posts[:limit]


# ==============================================================================
# 4. GDELT Project 2.0 DOC API
# ==============================================================================
def _get_last_gdelt_ts() -> float:
    global _LAST_GDELT_CALL_TS
    ts = _LAST_GDELT_CALL_TS
    try:
        if os.path.exists(_GDELT_TS_FILE):
            with open(_GDELT_TS_FILE, "r", encoding="utf-8") as f:
                file_ts = float(f.read().strip() or "0")
                ts = max(ts, file_ts)
    except Exception:
        pass
    return ts


def _set_last_gdelt_ts(ts: float) -> None:
    global _LAST_GDELT_CALL_TS
    _LAST_GDELT_CALL_TS = ts
    try:
        with open(_GDELT_TS_FILE, "w", encoding="utf-8") as f:
            f.write(str(ts))
    except Exception:
        pass


def search_gdelt_news(
    query: str,
    max_records: int = 10,
    timespan: str = "7d",
    fallback_to_google_news: bool = True
) -> List[Dict[str, Any]]:
    """
    Query the GDELT 2.0 Global Knowledge Graph / DOC API for global & multilingual news.
    Respects GDELT's 1-request-per-5-seconds rate limit (both in-process and cross-process)
    and falls back gracefully to Google News RSS if GDELT's public IP limiter returns HTTP 429.
    """
    max_records = max(0, max_records)
    if max_records == 0 or not query or not query.strip():
        return []

    last_ts = _get_last_gdelt_ts()
    elapsed = time.time() - last_ts
    if elapsed < 5.2 and last_ts > 0:
        time.sleep(5.2 - elapsed)

    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": query.strip(),
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(max(1, max_records), 250),
        "timespan": timespan
    }

    try:
        _set_last_gdelt_ts(time.time())
        resp = _session().get(url, params=params, timeout=15)
        if resp.status_code == 429 or "Please limit requests" in resp.text:
            time.sleep(5.3)
            _set_last_gdelt_ts(time.time())
            resp = _session().get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        if fallback_to_google_news:
            fb_items = search_google_news_rss(query=query.strip(), limit=max_records)
            if fb_items and "error" not in fb_items[0]:
                for item in fb_items:
                    if isinstance(item.get("extra"), dict):
                        item["extra"]["gdelt_fallback"] = True
                        item["extra"]["gdelt_notice"] = f"GDELT throttled ({e}); served via Google News RSS"
                return fb_items
        return [{
            "error": f"GDELT DOC 2.0 API request failed or rate-limited: {e}",
            "guidance": "GDELT enforces 1 request per 5 seconds per IP. Use search_google_news_rss for high-frequency queries.",
            "source": "gdelt"
        }]

    articles = data.get("articles", []) if isinstance(data, dict) else []
    if not isinstance(articles, list):
        articles = []

    results: List[Dict[str, Any]] = []
    for art in articles[:max_records]:
        if not isinstance(art, dict):
            continue
        item = NewsItem(
            title=art.get("title", ""),
            source=art.get("domain", "gdelt"),
            url=art.get("url", ""),
            published_at=art.get("seendate", ""),
            summary=art.get("title", ""),
            language=art.get("language", "unknown"),
            extra={
                "sourcecountry": art.get("sourcecountry"),
                "socialimage": art.get("socialimage"),
                "feed": "gdelt"
            }
        )
        results.append(item.to_dict())

    if not results and fallback_to_google_news:
        fb_items = search_google_news_rss(query=query.strip(), limit=max_records)
        if fb_items and "error" not in fb_items[0]:
            for item in fb_items:
                if isinstance(item.get("extra"), dict):
                    item["extra"]["gdelt_fallback"] = True
            return fb_items

    return results


search_gdelt = search_gdelt_news


# ==============================================================================
# 5. Unified News Search Contract
# ==============================================================================
def search_news(
    query: str = "",
    source: str = "google_news",
    lang: str = "auto",
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Unified multi-source news retrieval dispatcher.

    Args:
        query: Search keywords (optional when fetching a specific RSS portal or Telegram channel).
        source: 'google_news', 'gdelt', 'telegram' (or 'telegram:channel_name'),
                or any feed key in ISRAELI_RSS_FEEDS ('ynet', 'haaretz', 'mako', 'globes', 'c14', etc.).
        lang: 'auto', 'he', or 'en'.
        limit: Maximum items to return.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    src = source.lower().strip()
    if src in ("google_news", "google", "gnews"):
        effective_q = query.strip() or ("חדשות ישראל" if lang == "he" else "Israel technology")
        return search_google_news_rss(effective_q, lang=lang, limit=limit)
    elif src in ISRAELI_RSS_FEEDS:
        return fetch_israeli_rss(source=src, query=query or None, limit=limit)
    elif src.startswith("telegram"):
        chan = src.split(":", 1)[1] if ":" in src else (query if query and " " not in query else "amitsegal")
        q_filter = query if (":" in src or (query and " " in query)) else None
        return fetch_telegram_channel(channel=chan, query=q_filter, limit=limit)
    elif src == "gdelt":
        return search_gdelt_news(query=query or "Israel", max_records=limit)
    else:
        return [{
            "error": (
                f"Unsupported news source: '{source}'. Valid sources: 'google_news', 'gdelt', "
                f"'telegram:<channel>', or RSS feeds ({', '.join(sorted(ISRAELI_RSS_FEEDS.keys()))})."
            ),
            "source": source
        }]


# ==============================================================================
# 6. Model Context Protocol (MCP) & CLI Runner
# ==============================================================================
def run_mcp_server():
    """Hardened stdio Model Context Protocol (MCP) server for real-time news."""
    tools_def = [
        {
            "name": "search_news",
            "description": (
                "Search breaking news across Google News RSS (Hebrew/English), Israeli portals "
                "(ynet, haaretz, mako, globes, maariv, walla, c14, inn, bhol), Telegram channels, and GDELT."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords or topic"},
                    "source": {
                        "type": "string",
                        "default": "google_news",
                        "description": "google_news, ynet, haaretz, mako, globes, maariv, walla, c14, inn, bhol, telegram:amitsegal, or gdelt"
                    },
                    "lang": {"type": "string", "enum": ["auto", "he", "en"], "default": "auto"},
                    "limit": {"type": "integer", "default": 10}
                }
            }
        },
        {
            "name": "fetch_israeli_rss",
            "description": "Fetch live Hebrew/English articles from 12 Israeli news RSS portals (ynet, haaretz, israelhayom, maariv, walla, globes, themarker, kan, c14, inn, bhol, mako).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "default": "ynet", "description": "Portal key in ISRAELI_RSS_FEEDS"},
                    "query": {"type": "string", "description": "Optional keyword filter"},
                    "limit": {"type": "integer", "default": 10}
                }
            }
        },
        {
            "name": "fetch_telegram_channel",
            "description": "Fetch latest posts from a public Telegram channel web preview (e.g. amitsegal, abualiexpress).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "default": "amitsegal", "description": "Public channel handle"},
                    "query": {"type": "string", "description": "Optional keyword filter"},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["channel"]
            }
        }
    ]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: Invalid JSON"}
            }) + "\n")
            sys.stdout.flush()
            continue

        if not isinstance(req, dict):
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: Expected JSON object"}
            }) + "\n")
            sys.stdout.flush()
            continue

        method = req.get("method")
        msg_id = req.get("id")

        if method == "notifications/initialized" or (isinstance(method, str) and method.startswith("notifications/")) or (msg_id is None and method not in ["tools/call", "tools/list", "initialize"]):
            continue

        try:
            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "news-search-server", "version": "1.1.0"}
                    }
                }
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": tools_def}
                }
            elif method == "tools/call":
                params = req.get("params") or {}
                if not isinstance(params, dict):
                    params = {}
                name = params.get("name")
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}

                is_err = False
                if name in ("search_news", "news_search"):
                    out = search_news(
                        query=str(args.get("query", "")),
                        source=str(args.get("source", "google_news")),
                        lang=str(args.get("lang", "auto")),
                        limit=safe_int(args.get("limit"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name in ("fetch_israeli_rss", "israeli_rss"):
                    out = fetch_israeli_rss(
                        source=str(args.get("source", "ynet")),
                        query=args.get("query"),
                        limit=safe_int(args.get("limit"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name in ("fetch_telegram_channel", "telegram_channel"):
                    out = fetch_telegram_channel(
                        channel=str(args.get("channel", "amitsegal")),
                        query=args.get("query"),
                        limit=safe_int(args.get("limit"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                else:
                    out = {"error": f"Unknown tool: {name}"}
                    is_err = True

                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}],
                        "isError": is_err
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }
        except Exception as e:
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Internal server error: {e}"}
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    if "--mcp" in sys.argv:
        run_mcp_server()
    else:
        import argparse
        parser = argparse.ArgumentParser(description="Real-Time News & Media Search Tool")
        parser.add_argument("--query", "-q", type=str, default="", help="Search query")
        parser.add_argument("--source", "-s", type=str, default="ynet", help="News source (google_news, ynet, haaretz, mako, telegram:amitsegal, gdelt)")
        parser.add_argument("--lang", type=str, default="auto", choices=["auto", "he", "en"])
        parser.add_argument("--limit", "-l", type=int, default=5, help="Max results")
        args = parser.parse_args()

        res = search_news(query=args.query, source=args.source, lang=args.lang, limit=args.limit)
        print(json.dumps(res, ensure_ascii=False, indent=2))
