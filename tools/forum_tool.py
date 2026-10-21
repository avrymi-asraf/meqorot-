"""
forum_tool.py - Production Community Forums Access Client for AI Agents.

Supports:
- Reddit via Arctic Shift keyless community archive (posts, comments, historical slicing)
- Rotter.net Scoops RSS feed with mandatory Windows-1255 character decoding
- Universal Discourse search across developer communities (discuss.python.org, Hugging Face, OpenAI)
- Google SERP Dorking engine for Cloudflare-protected boards (FXP, Quora, Tapuz)

Provides:
- Standardized data models for forum threads and scoops
- Resilient connection pooling with exponential backoff
- Standalone Python functions and high-level `search_forum_serp_dork` contract
- CLI and stdio Model Context Protocol (MCP) server support
"""

import os
import sys
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, asdict, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (AgentDataSources/1.0)"
)

CANONICAL_SUBREDDITS: Dict[str, str] = {
    "israel": "Israel",
    "askreddit": "AskReddit",
    "worldnews": "worldnews",
    "palestine": "Palestine",
    "judaism": "Judaism",
    "hebrew": "hebrew",
    "python": "Python",
}


@dataclass
class ForumPost:
    """Standardized representation of a forum post/submission."""
    platform: str  # 'reddit', 'discourse', 'rotter', 'fxp', 'serp'
    post_id: str
    title: str
    author: str
    content: str
    url: str
    created_utc: Optional[Union[int, str]] = None
    score: Optional[int] = None
    num_comments: Optional[int] = None
    community: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RotterScoop:
    """Representation of a breaking scoop item from Rotter.net."""
    title: str
    link: str
    pub_date: str
    description: str = ""

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


def _clean_env(name: str) -> Optional[str]:
    """Read environment variable, ignoring .env.example placeholder values."""
    val = os.getenv(name)
    if not val or val.strip().startswith("your_"):
        return None
    return val.strip()


# ==============================================================================
# 1. Reddit via Arctic Shift API
# ==============================================================================
def search_reddit_arctic_shift(
    query: Optional[str] = None,
    subreddit: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Search Reddit submissions via the keyless Arctic Shift archive.

    Args:
        query: Search keywords.
        subreddit: Specific subreddit to search. Defaults to 'Israel' for Hebrew queries
                   or 'Python' for ASCII queries if omitted.
        limit: Number of items (max 100).

    Returns:
        List of standardized post dictionaries with title, author, score, permalink, selftext, created_utc.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    url = "https://arctic-shift.photon-reddit.com/api/posts/search"
    clean_sub = subreddit.strip().lstrip("r/").strip() if isinstance(subreddit, str) and subreddit.strip() else None
    clean_query = query.strip() if isinstance(query, str) and query.strip() else None

    # Handle query containing subreddit specification like 'r/programming'
    if clean_query and not clean_sub:
        sub_match = re.search(r"\br/([a-zA-Z0-9_]+)", clean_query)
        if sub_match:
            clean_sub = sub_match.group(1)
            clean_query = re.sub(r"\br/[a-zA-Z0-9_]+", "", clean_query).strip() or None

    is_hebrew = bool(clean_query and re.search(r"[\u0590-\u05FF]", clean_query))

    # Default subreddit to 'Israel' for Hebrew queries or 'Python' when omitted
    if not clean_sub:
        clean_sub = "Israel" if is_hebrew else "Python"

    clean_sub = CANONICAL_SUBREDDITS.get(clean_sub.lower(), clean_sub)

    if clean_query and not is_hebrew:
        after_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 240 * 86400))
        params: Dict[str, Any] = {
            "limit": min(max(1, limit), 100),
            "title": clean_query,
            "after": after_date,
            "subreddit": clean_sub,
        }
    elif clean_query and is_hebrew:
        # Arctic Shift PostgreSQL FTS uses English dictionary and times out (422) on Hebrew;
        # fetch recent posts from target subreddit and filter client-side.
        params = {"subreddit": clean_sub, "limit": 100}
    else:
        params = {"subreddit": clean_sub, "limit": min(max(1, limit), 100)}

    data: Any = {}
    used_fallback = "title" not in params
    q_words = clean_query.split() if clean_query and not is_hebrew else []
    try:
        resp = _session().get(url, params=params, timeout=12)
        if "title" in params:
            need_fallback = resp.status_code == 422
            if not need_fallback and resp.status_code == 200:
                parsed_initial = resp.json()
                if isinstance(parsed_initial, dict) and not parsed_initial.get("data"):
                    need_fallback = True
                else:
                    data = parsed_initial
            if need_fallback:
                used_fallback = True
                # For multi-word ASCII queries, first narrow by primary keyword in title with limit=25
                if len(q_words) > 1:
                    kw_params = {
                        "subreddit": clean_sub,
                        "title": q_words[0],
                        "after": params["after"],
                        "limit": 25,
                    }
                    kw_resp = _session().get(url, params=kw_params, timeout=12)
                    if kw_resp.status_code == 200:
                        kw_data = kw_resp.json()
                        if isinstance(kw_data, dict) and kw_data.get("data"):
                            data = kw_data
                            need_fallback = False
                if need_fallback:
                    fallback_params = {"subreddit": clean_sub, "limit": 100}
                    resp = _session().get(url, params=fallback_params, timeout=12)
                    resp.raise_for_status()
                    data = resp.json()
            elif resp.status_code != 200:
                resp.raise_for_status()
        else:
            resp.raise_for_status()
            data = resp.json()
    except requests.RequestException as e:
        if "title" in params:
            try:
                used_fallback = True
                fallback_params = {"subreddit": clean_sub, "limit": 100}
                fallback_resp = _session().get(url, params=fallback_params, timeout=12)
                fallback_resp.raise_for_status()
                data = fallback_resp.json()
            except Exception:
                return [{"error": f"Arctic Shift request failed: {e}", "platform": "reddit"}]
        else:
            return [{"error": f"Arctic Shift request failed: {e}", "platform": "reddit"}]

    if not isinstance(data, dict):
        data = {}

    posts = data.get("data", []) or []
    if not isinstance(posts, list):
        posts = []

    def _matches_query(p: Any, q_low: str, q_toks: List[str]) -> bool:
        if not isinstance(p, dict):
            return False
        haystack = (str(p.get("title", "")) + " " + str(p.get("selftext") or "")).lower()
        if q_low in haystack:
            return True
        return bool(q_toks) and all(t in haystack for t in q_toks)

    # Unconditionally filter posts when query is non-empty (never return unfiltered posts)
    if clean_query:
        q_lower = clean_query.lower()
        q_tokens = [t for t in re.split(r"\s+", q_lower) if t]
        matched = [p for p in posts if _matches_query(p, q_lower, q_tokens)]
        current_batch = posts
        # If initial search did not find enough matches, scan recent pages via B-tree index
        if len(matched) < limit and not used_fallback:
            try:
                fb_resp = _session().get(url, params={"subreddit": clean_sub, "limit": 100}, timeout=10)
                if fb_resp.status_code == 200:
                    fb_data = fb_resp.json()
                    current_batch = (fb_data.get("data") or []) if isinstance(fb_data, dict) else []
                    seen_ids = {p.get("id") for p in matched if isinstance(p, dict)}
                    for p in current_batch:
                        if _matches_query(p, q_lower, q_tokens) and p.get("id") not in seen_ids:
                            matched.append(p)
                            seen_ids.add(p.get("id"))
            except Exception:
                pass

        pages_scanned = 0
        seen_ids = {p.get("id") for p in matched if isinstance(p, dict)}
        while len(matched) < limit and isinstance(current_batch, list) and len(current_batch) >= 50 and pages_scanned < 2:
            pages_scanned += 1
            last_ts = current_batch[-1].get("created_utc") if isinstance(current_batch[-1], dict) else None
            if not isinstance(last_ts, (int, float)):
                break
            before_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(last_ts) - 1))
            page_params = {"subreddit": clean_sub, "limit": 100, "before": before_iso}
            try:
                page_resp = _session().get(url, params=page_params, timeout=10)
                if page_resp.status_code != 200:
                    break
                page_data = page_resp.json()
                current_batch = (page_data.get("data") or []) if isinstance(page_data, dict) else []
                if not isinstance(current_batch, list) or not current_batch:
                    break
                for p in current_batch:
                    if _matches_query(p, q_lower, q_tokens) and p.get("id") not in seen_ids:
                        matched.append(p)
                        seen_ids.add(p.get("id"))
            except Exception:
                break
        posts = matched[:limit]

    results: List[Dict[str, Any]] = []

    for item in posts[:limit]:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id", ""))
        permalink = item.get("permalink", "")
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"

        post = ForumPost(
            platform="reddit",
            post_id=pid,
            title=item.get("title", ""),
            author=item.get("author", "[deleted]"),
            content=item.get("selftext", "") or "",
            url=permalink,
            created_utc=item.get("created_utc"),
            score=item.get("score", 0),
            num_comments=item.get("num_comments", 0),
            community=f"r/{item.get('subreddit', clean_sub or 'unknown')}",
            extra={"upvote_ratio": item.get("upvote_ratio")}
        )
        results.append(post.to_dict())

    return results


search_arctic_shift = search_reddit_arctic_shift
fetch_reddit_arctic_shift = search_reddit_arctic_shift


# ==============================================================================
# 2. Rotter.net Scoops RSS (Mandatory Windows-1255 Decoding)
# ==============================================================================
def fetch_rotter_scoops(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch breaking scoops from Rotter.net RSS with Windows-1255 character decoding.

    CRITICAL ARCHITECTURAL REQUIREMENT:
    Rotter.net encodes Hebrew text in legacy 'windows-1255'. Standard UTF-8 decoding
    raises UnicodeDecodeError. This client decodes raw bytes explicitly.

    Args:
        limit: Max scoops to return (default 10).

    Returns:
        List of dictionaries with title, link, description, pubDate.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    url = "https://rotter.net/rss/rotternews.xml"
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/xml, text/xml"}

    try:
        resp = _session().get(url, headers=headers, timeout=12)
        resp.raise_for_status()

        # CRITICAL: Decode raw bytes using windows-1255
        raw_bytes = resp.content
        decoded_xml = raw_bytes.decode("windows-1255", errors="replace")
        root = ET.fromstring(decoded_xml)
    except requests.RequestException as e:
        return [{"error": f"Rotter RSS HTTP request failed: {e}", "platform": "rotter"}]
    except ET.ParseError as e:
        return [{"error": f"Rotter RSS XML parsing failed: {e}", "platform": "rotter"}]

    items = root.findall(".//item")
    scoops: List[Dict[str, Any]] = []

    for item in items[:limit]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date = item.findtext("pubDate") or ""
        desc = item.findtext("description") or ""

        scoop = RotterScoop(
            title=title.strip(),
            link=link.strip(),
            pub_date=pub_date.strip(),
            description=desc.strip()
        )
        scoops.append(scoop.to_dict())

    return scoops


# ==============================================================================
# 3. Google SERP Dorking Builder for Forums (FXP, Quora, Tapuz)
# ==============================================================================
def build_serp_forum_dork(
    query: str,
    domain: str = "fxp.co.il",
    search_engine_url: Optional[str] = None,
    api_key: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Build an advanced Google Dork query and optionally execute via SERP provider.

    Args:
        query: Topic or keywords to search.
        domain: Forum domain to target (e.g. 'fxp.co.il', 'quora.com', 'tapuz.co.il').
        search_engine_url: Optional custom search endpoint (e.g. SearXNG) or SERP URL.
        api_key: Optional API key for execution.
        limit: Max results.

    Returns:
        Dictionary containing constructed dork query, search URL, and results if executed.
    """
    clean_domain = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
    dork_query = f"site:{clean_domain} {query}"
    encoded_query = urllib.parse.quote_plus(dork_query)
    google_url = f"https://www.google.com/search?q={encoded_query}"

    output: Dict[str, Any] = {
        "dork_query": dork_query,
        "target_domain": clean_domain,
        "google_search_url": google_url,
        "guidance": (
            f"Query bypasses {clean_domain} Cloudflare Turnstile bot challenges by leveraging "
            "Google's server-rendered web cache. Can be executed via Google Custom Search (PSE), "
            "SerpApi, or a custom search_engine_url."
        ),
        "results": []
    }

    if limit <= 0:
        return output

    clean_api_key = api_key if (api_key and not str(api_key).startswith("your_")) else None
    cx = _clean_env("GOOGLE_PSE_CX")
    pse_key = (clean_api_key if (clean_api_key and cx) else _clean_env("GOOGLE_PSE_KEY"))
    serpapi_key = (clean_api_key if (clean_api_key and not cx) else _clean_env("SERPAPI_API_KEY"))

    if search_engine_url:
        params: Dict[str, Any] = {"q": dork_query, "format": "json"}
        headers: Dict[str, str] = {}
        if clean_api_key:
            headers["Authorization"] = f"Bearer {clean_api_key}"
        try:
            r = _session().get(search_engine_url, params=params, headers=headers or None, timeout=12)
            r.raise_for_status()
            custom_data = r.json()
            raw_list = (
                custom_data.get("results")
                or custom_data.get("organic_results")
                or custom_data.get("items")
                or []
            ) if isinstance(custom_data, dict) else []
            for it in raw_list[:limit]:
                if isinstance(it, dict):
                    output["results"].append({
                        "title": it.get("title"),
                        "link": it.get("url") or it.get("link"),
                        "snippet": it.get("content") or it.get("snippet")
                    })
        except Exception as e:
            output["execution_error"] = str(e)
    elif pse_key and cx:
        # Execute Google Programmable Search Engine (PSE)
        pse_url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": pse_key, "cx": cx, "q": dork_query, "num": min(max(1, limit), 10)}
        try:
            r = _session().get(pse_url, params=params, timeout=12)
            r.raise_for_status()
            pse_data = r.json()
            items = pse_data.get("items", []) if isinstance(pse_data, dict) else []
            for it in items[:limit]:
                if isinstance(it, dict):
                    output["results"].append({
                        "title": it.get("title"),
                        "link": it.get("link"),
                        "snippet": it.get("snippet")
                    })
        except Exception as e:
            output["execution_error"] = str(e)
    elif serpapi_key:
        # Execute SerpApi Google Search
        serp_url = "https://serpapi.com/search.json"
        params = {
            "api_key": serpapi_key,
            "engine": "google",
            "q": dork_query,
            "num": min(max(1, limit), 10)
        }
        try:
            r = _session().get(serp_url, params=params, timeout=12)
            r.raise_for_status()
            serp_data = r.json()
            items = serp_data.get("organic_results", []) if isinstance(serp_data, dict) else []
            for it in items[:limit]:
                if isinstance(it, dict):
                    output["results"].append({
                        "title": it.get("title"),
                        "link": it.get("link"),
                        "snippet": it.get("snippet")
                    })
        except Exception as e:
            output["execution_error"] = str(e)

    return output


# ==============================================================================
# 4. Discourse Universal Search & StackExchange API
# ==============================================================================
def search_discourse_forum(
    base_url: str,
    query: str,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Universal search across any Discourse forum instance.

    Args:
        base_url: Discourse forum base URL (e.g. 'https://discuss.python.org').
        query: Search keywords (supports operators like 'status:solved', 'order:latest').
        limit: Number of posts to retrieve (default 5).

    Returns:
        List of standardized post dictionaries.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    clean_base = base_url.rstrip("/")
    url = f"{clean_base}/search.json"
    params = {"q": query}
    headers = {"Accept": "application/json"}

    try:
        resp = _session().get(url, params=params, headers=headers, timeout=12)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"Discourse request to {clean_base} failed: {e}",
            "platform": "discourse"
        }]

    if not isinstance(data, dict):
        data = {}

    posts = data.get("posts", [])
    if not isinstance(posts, list):
        posts = []
    topics_list = data.get("topics", [])
    topics = {t.get("id"): t for t in topics_list if isinstance(t, dict)} if isinstance(topics_list, list) else {}

    results: List[Dict[str, Any]] = []
    for p in posts[:limit]:
        if not isinstance(p, dict):
            continue
        tid = p.get("topic_id")
        topic = topics.get(tid, {})
        title = topic.get("title") or f"Topic #{tid}"
        slug = topic.get("slug") or str(tid)
        post_num = p.get("post_number", 1)
        post_url = f"{clean_base}/t/{slug}/{tid}/{post_num}"

        post = ForumPost(
            platform="discourse",
            post_id=str(p.get("id")),
            title=title,
            author=p.get("username", "unknown"),
            content=p.get("blurb", "") or "",
            url=post_url,
            created_utc=p.get("created_at"),
            score=p.get("like_count", 0),
            community=clean_base,
            extra={"topic_id": tid, "post_number": post_num}
        )
        results.append(post.to_dict())

    return results


def search_stackexchange(
    query: str,
    site: str = "stackoverflow",
    limit: int = 5,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search StackExchange network sites (StackOverflow, ServerFault, MiYodeya/judaism, etc.)
    via the official StackExchange v2.3 API.
    """
    limit = max(0, limit)
    if limit == 0 or not query or not query.strip():
        return []

    url = "https://api.stackexchange.com/2.3/search/advanced"
    params: Dict[str, Any] = {
        "order": "desc",
        "sort": "relevance",
        "q": query.strip(),
        "site": site.strip() or "stackoverflow",
        "pagesize": min(max(1, limit), 100),
    }
    resolved_key = (api_key if (api_key and not str(api_key).startswith("your_")) else _clean_env("STACKEXCHANGE_API_KEY"))
    if resolved_key:
        params["key"] = resolved_key

    try:
        resp = _session().get(url, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{"error": f"StackExchange request failed: {e}", "platform": "stackexchange"}]

    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []

    results: List[Dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id", ""))
        owner = item.get("owner", {}) if isinstance(item.get("owner"), dict) else {}
        post = ForumPost(
            platform="stackexchange",
            post_id=qid,
            title=item.get("title", ""),
            author=owner.get("display_name", "unknown"),
            content=", ".join(item.get("tags", [])) if isinstance(item.get("tags"), list) else "",
            url=item.get("link", f"https://{site}.com/q/{qid}"),
            created_utc=item.get("creation_date"),
            score=item.get("score", 0),
            num_comments=item.get("answer_count", 0),
            community=site,
            extra={
                "is_answered": item.get("is_answered", False),
                "view_count": item.get("view_count", 0),
                "tags": item.get("tags", []),
            }
        )
        results.append(post.to_dict())

    return results


fetch_stackexchange = search_stackexchange


# ==============================================================================
# 5. High-Level Unified Contract (PROJECT.md)
# ==============================================================================
def search_forum_serp_dork(
    query: str,
    forum_domain: str = "fxp.co.il",
    limit: int = 10,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    High-level forum SERP dork search contract for Cloudflare-protected boards.

    Args:
        query: Search query
        forum_domain: e.g. 'fxp.co.il', 'quora.com', 'rotter.net'
        limit: Result limit
        api_key: Optional SERP provider API key

    Returns:
        List of structured post dictionaries or guidance dictionary.
    """
    limit = max(0, limit)
    dork_info = build_serp_forum_dork(
        query=query,
        domain=forum_domain,
        api_key=api_key,
        limit=limit
    )

    if dork_info.get("results"):
        results: List[Dict[str, Any]] = []
        for it in dork_info["results"]:
            results.append({
                "platform": "serp_forum",
                "title": it.get("title", ""),
                "url": it.get("link", ""),
                "content": it.get("snippet", ""),
                "domain": forum_domain,
                "dork_query": dork_info["dork_query"]
            })
        return results

    # Return structured dork payload for agent consumption
    return [{
        "platform": "serp_forum",
        "dork_query": dork_info["dork_query"],
        "target_domain": forum_domain,
        "google_search_url": dork_info["google_search_url"],
        "guidance": dork_info["guidance"]
    }]


# ==============================================================================
# 6. Model Context Protocol (MCP) & CLI Runner
# ==============================================================================
def run_mcp_server():
    """Robust stdio Model Context Protocol (MCP) server for forum extraction."""
    tools_def = [
        {
            "name": "search_reddit_arctic_shift",
            "description": "Search Reddit submissions via the keyless Arctic Shift archive.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search"},
                    "subreddit": {"type": "string", "description": "Subreddit name (e.g. 'python' or 'Israel')"},
                    "limit": {"type": "integer", "default": 10}
                }
            }
        },
        {
            "name": "fetch_rotter_scoops",
            "description": "Fetch real-time breaking scoops from Rotter.net news RSS with Windows-1255 decoding.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10}
                }
            }
        },
        {
            "name": "search_discourse_forum",
            "description": "Search any developer Discourse forum (e.g. discuss.python.org, Hugging Face).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Discourse forum base URL"},
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5}
                },
                "required": ["base_url", "query"]
            }
        },
        {
            "name": "search_stackexchange",
            "description": "Search StackExchange Q&A sites (stackoverflow, judaism, serverfault, etc.).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "site": {"type": "string", "default": "stackoverflow", "description": "StackExchange site parameter"},
                    "limit": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        },
        {
            "name": "build_serp_forum_dork",
            "description": "Construct precision Google Dork query for Cloudflare-protected boards (FXP, Quora).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search topic"},
                    "domain": {"type": "string", "default": "fxp.co.il", "description": "Forum domain"},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["query"]
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

        # Ignore notifications (JSON-RPC 2.0: Server MUST NOT reply to notifications)
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
                        "serverInfo": {"name": "forum-search-server", "version": "1.1.0"}
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
                if name == "search_reddit_arctic_shift":
                    out = search_reddit_arctic_shift(
                        query=args.get("query"),
                        subreddit=args.get("subreddit"),
                        limit=safe_int(args.get("limit"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "fetch_rotter_scoops":
                    out = fetch_rotter_scoops(limit=safe_int(args.get("limit"), 10))
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "search_discourse_forum":
                    out = search_discourse_forum(
                        base_url=str(args.get("base_url", "https://discuss.python.org")),
                        query=str(args.get("query", "")),
                        limit=safe_int(args.get("limit"), 5)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "search_stackexchange":
                    out = search_stackexchange(
                        query=str(args.get("query", "")),
                        site=str(args.get("site", "stackoverflow")),
                        limit=safe_int(args.get("limit"), 5)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "build_serp_forum_dork":
                    out = build_serp_forum_dork(
                        query=str(args.get("query", "")),
                        domain=str(args.get("domain", "fxp.co.il")),
                        search_engine_url=args.get("search_engine_url"),
                        limit=safe_int(args.get("limit"), 10)
                    )
                    if isinstance(out, dict) and "error" in out:
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
        parser = argparse.ArgumentParser(description="Forum Search Client")
        parser.add_argument("--platform", "-p", type=str, default="rotter", choices=["reddit", "rotter", "discourse", "stackexchange", "dork"])
        parser.add_argument("--query", "-q", type=str, default="asyncio", help="Search query")
        parser.add_argument("--subreddit", "-s", type=str, default="python", help="Subreddit name or StackExchange site")
        parser.add_argument("--limit", "-l", type=int, default=3, help="Max results")
        args = parser.parse_args()

        if args.platform == "reddit":
            res = search_reddit_arctic_shift(query=args.query, subreddit=args.subreddit, limit=args.limit)
        elif args.platform == "rotter":
            res = fetch_rotter_scoops(limit=args.limit)
        elif args.platform == "discourse":
            res = search_discourse_forum("https://discuss.python.org", query=args.query, limit=args.limit)
        elif args.platform == "stackexchange":
            site_arg = args.subreddit if args.subreddit != "python" else "stackoverflow"
            res = search_stackexchange(query=args.query, site=site_arg, limit=args.limit)
        else:
            res = build_serp_forum_dork(query=args.query, domain="fxp.co.il")

        print(json.dumps(res, ensure_ascii=False, indent=2))
