"""
social_tool.py - Production Social Networks Access Client for AI Agents.

Supports:
- SocialData.tools (X.com / Twitter search, profiles, tweets, threads with lang:he)
- Exa.ai Neural Search (semantic retrieval filtered to x.com, instagram.com, facebook.com)
- Apify Actor Client (Instagram & Facebook profile/post extraction)

Provides:
- Unified schema: platform, author, content, timestamp, url, metrics
- Resilient retries and clear auth/quota guidance
- Standalone Python functions and high-level `search_social` contract
- CLI and stdio Model Context Protocol (MCP) server support
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, asdict, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = "AgentDataSources-SocialClient/1.0 (+https://github.com/agent-data-sources)"


@dataclass
class SocialPost:
    """Standardized representation of a social media item across platforms."""
    platform: str  # 'x', 'instagram', 'facebook'
    post_id: str
    author: str
    content: str
    timestamp: str
    url: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["engagement"] = dict(self.metrics)
        return d


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


def _clean_env(val: Optional[str]) -> Optional[str]:
    """Return None if env var is empty or an unconfigured .env.example placeholder."""
    if not val:
        return None
    v = val.strip()
    if not v or v.lower().startswith("your_"):
        return None
    return v


# ==============================================================================
# 1. SocialData.tools API (X.com / Twitter)
# ==============================================================================
def search_socialdata_tweets(
    query: str,
    api_key: Optional[str] = None,
    max_results: int = 10,
    search_type: str = "Top"
) -> List[Dict[str, Any]]:
    """
    Search X.com / Twitter posts via the SocialData.tools API.

    Args:
        query: Full Twitter advanced search query (supports lang:he, min_faves:X, etc.)
        api_key: SocialData API Key. Defaults to SOCIALDATA_API_KEY env var.
        max_results: Number of tweets to retrieve (default 10).
        search_type: 'Top' (default) or 'Latest'.

    Returns:
        List of standardized post dictionaries.
    """
    max_results = max(0, max_results)
    if max_results == 0:
        return []

    token = _clean_env(api_key) or _clean_env(os.getenv("SOCIALDATA_API_KEY"))
    if not token:
        return [{
            "error": "Missing SocialData API Key",
            "guidance": (
                "SocialData.tools provides access to X.com at $0.20/1,000 tweets. "
                "Set SOCIALDATA_API_KEY in your environment or pass api_key parameter."
            ),
            "platform": "x",
            "offline_sample": [
                {
                    "platform": "x",
                    "post_id": "sample_12345",
                    "author": "TechLeaderIL",
                    "content": "התקדמות אדירה בתחום הבינה המלאכותית בישראל 🚀",
                    "timestamp": "2026-10-20T12:00:00Z",
                    "url": "https://x.com/TechLeaderIL/status/sample_12345",
                    "metrics": {"likes": 142, "retweets": 28, "replies": 9},
                    "engagement": {"likes": 142, "retweets": 28, "replies": 9},
                }
            ]
        }]

    endpoint = "https://api.socialdata.tools/twitter/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    params = {
        "query": query,
        "type": search_type
    }

    try:
        resp = _session().get(endpoint, headers=headers, params=params, timeout=15)
        if resp.status_code == 401 or resp.status_code == 403:
            return [{
                "error": f"SocialData Authentication Failed (HTTP {resp.status_code})",
                "guidance": "Verify that your SOCIALDATA_API_KEY is active and has available credits.",
                "platform": "x"
            }]
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"SocialData request failed: {e}",
            "platform": "x"
        }]

    if not isinstance(data, dict):
        data = {}

    tweets = data.get("tweets", [])
    if not isinstance(tweets, list):
        tweets = []
    results: List[Dict[str, Any]] = []

    for t in tweets[:max_results]:
        if not isinstance(t, dict):
            continue
        user = t.get("user", {})
        tid = str(t.get("id_str") or t.get("id", ""))
        screen_name = user.get("screen_name", "unknown")
        created_at = t.get("tweet_created_at") or t.get("created_at", "")

        post = SocialPost(
            platform="x",
            post_id=tid,
            author=f"@{screen_name}",
            content=t.get("full_text") or t.get("text", ""),
            timestamp=created_at,
            url=f"https://x.com/{screen_name}/status/{tid}",
            metrics={
                "likes": t.get("favorite_count", 0),
                "retweets": t.get("retweet_count", 0),
                "replies": t.get("reply_count", 0),
                "views": t.get("views_count", 0)
            },
            extra={
                "lang": t.get("lang"),
                "author_name": user.get("name"),
                "verified": user.get("verified", False)
            }
        )
        results.append(post.to_dict())

    return results


# ==============================================================================
# 2. Exa.ai Neural Social Search
# ==============================================================================
def search_exa_social(
    query: str,
    include_domains: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    num_results: int = 5
) -> List[Dict[str, Any]]:
    """
    Search social media platforms via Exa.ai neural embeddings search.

    Args:
        query: Natural language or topic query.
        include_domains: Domains to filter (defaults to ['x.com', 'instagram.com', 'facebook.com']).
        api_key: Exa API Key. Defaults to EXA_API_KEY env var.
        num_results: Maximum results to return (default 5).

    Returns:
        List of standardized post dictionaries.
    """
    num_results = max(0, num_results)
    if num_results == 0:
        return []

    token = _clean_env(api_key) or _clean_env(os.getenv("EXA_API_KEY"))
    if not token:
        return [{
            "error": "Missing EXA_API_KEY",
            "guidance": (
                "Exa.ai provides neural search over crawled web and social media. "
                "Set EXA_API_KEY in your environment to execute live semantic queries."
            ),
            "platform": "social_web"
        }]

    domains = include_domains or ["x.com", "instagram.com", "facebook.com"]
    endpoint = "https://api.exa.ai/searchAndContents"
    headers = {
        "x-api-key": token,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "query": query,
        "includeDomains": domains,
        "numResults": num_results,
        "type": "neural",
        "contents": {
            "text": {"maxCharacters": 1500},
            "highlights": {"numSentences": 2}
        }
    }

    try:
        resp = _session().post(endpoint, headers=headers, json=payload, timeout=20)
        if resp.status_code in (401, 403):
            return [{
                "error": f"Exa.ai Authentication Failed (HTTP {resp.status_code})",
                "guidance": "Check that your EXA_API_KEY is valid at https://dashboard.exa.ai",
                "platform": "social_web"
            }]
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"Exa.ai request failed: {e}",
            "platform": "social_web"
        }]

    if not isinstance(data, dict):
        data = {}

    results: List[Dict[str, Any]] = []
    items_list = data.get("results", [])
    if not isinstance(items_list, list):
        items_list = []

    for item in items_list[:num_results]:
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        # Detect platform from URL
        platform = "social_web"
        if "x.com" in url or "twitter.com" in url:
            platform = "x"
        elif "instagram.com" in url:
            platform = "instagram"
        elif "facebook.com" in url:
            platform = "facebook"

        highlights = item.get("highlights", [])
        content = item.get("text", "")
        if highlights:
            content = " [...] ".join(highlights)

        post = SocialPost(
            platform=platform,
            post_id=item.get("id", url),
            author=item.get("author") or "Unknown Author",
            content=content,
            timestamp=item.get("publishedDate") or "",
            url=url,
            metrics={"score": item.get("score")},
            extra={"title": item.get("title")}
        )
        results.append(post.to_dict())

    return results


# ==============================================================================
# 3. Apify Actor Client (Instagram / Facebook)
# ==============================================================================
def fetch_apify_instagram(
    username: str,
    api_key: Optional[str] = None,
    max_posts: int = 5
) -> List[Dict[str, Any]]:
    """
    Fetch public Instagram posts for a given username using Apify's instagram-scraper actor.

    Args:
        username: Target Instagram handle (e.g. 'instagram' or 'israel').
        api_key: Apify API token. Defaults to APIFY_API_TOKEN env var.
        max_posts: Number of posts to retrieve (default 5).

    Returns:
        List of standardized post dictionaries.
    """
    max_posts = max(0, max_posts)
    if max_posts == 0:
        return []

    token = _clean_env(api_key) or _clean_env(os.getenv("APIFY_API_TOKEN")) or _clean_env(os.getenv("APIFY_TOKEN"))
    clean_user = username.lstrip("@").strip()

    if not token:
        return [{
            "error": "Missing Apify API Token",
            "guidance": (
                "Apify Instagram scraper overcomes Instagram login walls and residential proxy challenges. "
                "Set APIFY_API_TOKEN or pass api_key parameter to run."
            ),
            "platform": "instagram"
        }]

    endpoint = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"
    params = {"token": token}
    payload = {
        "directUrls": [f"https://www.instagram.com/{clean_user}/"],
        "resultsType": "posts",
        "resultsLimit": max_posts
    }

    try:
        resp = _session().post(endpoint, params=params, json=payload, timeout=60)
        if resp.status_code in (401, 403):
            return [{
                "error": f"Apify Authentication Failed (HTTP {resp.status_code})",
                "guidance": "Check that your APIFY_API_TOKEN is valid at https://console.apify.com",
                "platform": "instagram"
            }]
        resp.raise_for_status()
        items = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"Apify Instagram actor run failed: {e}",
            "platform": "instagram"
        }]

    results: List[Dict[str, Any]] = []
    if isinstance(items, list):
        for it in items[:max_posts]:
            if not isinstance(it, dict):
                continue
            pid = it.get("id") or it.get("shortCode", "")
            post = SocialPost(
                platform="instagram",
                post_id=str(pid),
                author=f"@{clean_user}",
                content=it.get("caption", ""),
                timestamp=it.get("timestamp", ""),
                url=it.get("url") or f"https://www.instagram.com/p/{it.get('shortCode')}/",
                metrics={
                    "likes": it.get("likesCount", 0),
                    "comments": it.get("commentsCount", 0),
                    "video_views": it.get("videoViewCount")
                },
                extra={
                    "dimensions": it.get("dimensions"),
                    "is_video": it.get("isVideo", False)
                }
            )
            results.append(post.to_dict())

    return results


def fetch_apify_facebook(
    page_or_query: str,
    api_key: Optional[str] = None,
    max_posts: int = 5
) -> List[Dict[str, Any]]:
    """
    Fetch public Facebook Page posts using Apify's facebook-posts-scraper actor.

    Args:
        page_or_query: Facebook page handle or full URL (e.g. 'ynetnews' or 'https://www.facebook.com/ynetnews').
        api_key: Apify API token. Defaults to APIFY_API_TOKEN env var.
        max_posts: Number of posts to retrieve (default 5).

    Returns:
        List of standardized post dictionaries.
    """
    max_posts = max(0, max_posts)
    if max_posts == 0:
        return []

    token = _clean_env(api_key) or _clean_env(os.getenv("APIFY_API_TOKEN")) or _clean_env(os.getenv("APIFY_TOKEN"))
    clean_target = page_or_query.strip()
    if not clean_target.startswith("http"):
        clean_target = f"https://www.facebook.com/{clean_target.lstrip('@')}/"

    if not token:
        return [{
            "error": "Missing Apify API Token",
            "guidance": (
                "Apify Facebook Posts Scraper overcomes Facebook login walls on public pages. "
                "Set APIFY_API_TOKEN or pass api_key parameter to run."
            ),
            "platform": "facebook"
        }]

    endpoint = "https://api.apify.com/v2/acts/apify~facebook-posts-scraper/run-sync-get-dataset-items"
    params = {"token": token}
    payload = {
        "startUrls": [{"url": clean_target}],
        "resultsLimit": max_posts
    }

    try:
        resp = _session().post(endpoint, params=params, json=payload, timeout=60)
        if resp.status_code in (401, 403):
            return [{
                "error": f"Apify Authentication Failed (HTTP {resp.status_code})",
                "guidance": "Check that your APIFY_API_TOKEN is valid at https://console.apify.com",
                "platform": "facebook"
            }]
        resp.raise_for_status()
        items = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"Apify Facebook actor run failed: {e}",
            "platform": "facebook"
        }]

    results: List[Dict[str, Any]] = []
    if isinstance(items, list):
        for it in items[:max_posts]:
            if not isinstance(it, dict):
                continue
            pid = it.get("postId") or it.get("id", "")
            author_name = it.get("pageName") or page_or_query
            post = SocialPost(
                platform="facebook",
                post_id=str(pid),
                author=str(author_name),
                content=it.get("text", ""),
                timestamp=it.get("time") or it.get("timestamp", ""),
                url=it.get("url") or clean_target,
                metrics={
                    "likes": it.get("likes", 0),
                    "comments": it.get("comments", 0),
                    "shares": it.get("shares", 0)
                }
            )
            results.append(post.to_dict())

    return results


# ==============================================================================
# 4. Personal Authenticated Account Readers (X / Twitter & Instagram)
# ==============================================================================
X_WEB_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)


def fetch_personal_x_timeline(
    mode: str = "bookmarks",
    auth_token: Optional[str] = None,
    ct0: Optional[str] = None,
    max_results: int = 10,
    screen_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Read the user's personal X.com (Twitter) Bookmarks, Home Timeline, or UserTweets using
    authenticated session cookies (X_AUTH_TOKEN and X_CT0).

    Args:
        mode: 'bookmarks', 'timeline', or 'user_tweets'.
        auth_token: Value of 'auth_token' cookie from x.com (or X_AUTH_TOKEN env var).
        ct0: Value of 'ct0' CSRF cookie from x.com (or X_CT0 env var).
        max_results: Maximum tweets to return.
        screen_name: Optional handle when mode='user_tweets'.
    """
    max_results = max(0, max_results)
    if max_results == 0:
        return []

    resolved_auth = _clean_env(auth_token) or _clean_env(os.getenv("X_AUTH_TOKEN"))
    resolved_ct0 = _clean_env(ct0) or _clean_env(os.getenv("X_CT0"))

    if not resolved_auth or not resolved_ct0:
        return [{
            "error": "Missing Personal X Session Cookies (X_AUTH_TOKEN / X_CT0)",
            "guidance": (
                "To read your personal X/Twitter account (Bookmarks, Home Timeline, private lists):\n"
                "1. Open https://x.com in your browser while logged in.\n"
                "2. Open DevTools (F12) -> Application -> Cookies -> https://x.com.\n"
                "3. Copy the values of 'auth_token' and 'ct0'.\n"
                "4. Set X_AUTH_TOKEN and X_CT0 in your .env file (or use a logged-in WSL Chrome profile via chrome-devtools-mcp)."
            ),
            "platform": "x",
            "mode": mode
        }]

    headers = {
        "Authorization": f"Bearer {X_WEB_BEARER}",
        "Cookie": f"auth_token={resolved_auth}; ct0={resolved_ct0}",
        "x-csrf-token": resolved_ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    clean_mode = (mode or "bookmarks").lower().strip()
    if clean_mode == "bookmarks":
        endpoint = "https://x.com/i/api/graphql/uNowfj04D8HFVFMbJM6xrQ/Bookmarks"
    elif clean_mode in ("user_tweets", "usertweets", "user"):
        endpoint = "https://x.com/i/api/graphql/V7H0Ap3_Hh2FyS75OCDO3Q/UserTweets"
    else:
        endpoint = "https://x.com/i/api/graphql/HJFjzBgCs16TqxewQOeLNg/HomeTimeline"

    variables: Dict[str, Any] = {"count": min(max_results, 40), "includePromotedContent": False}
    if screen_name:
        variables["screen_name"] = screen_name.lstrip("@").strip()
    features = {
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_Enhance_cards_enabled": False,
    }
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps(features),
    }

    data: Any = None
    try:
        resp = _session().get(endpoint, headers=headers, params=params, timeout=15)
        if resp.status_code in (401, 403):
            return [{
                "error": f"Personal X Session Authentication Failed (HTTP {resp.status_code})",
                "guidance": "Your X_AUTH_TOKEN or X_CT0 cookie has expired or was rotated. Refresh cookies from x.com DevTools.",
                "platform": "x",
                "mode": mode
            }]
        # Fallback to v1.1 adaptive timeline if GraphQL queryId rotated (HTTP 400/404)
        if resp.status_code in (400, 404) and clean_mode != "bookmarks":
            v1_url = (
                "https://api.x.com/1.1/statuses/user_timeline.json"
                if clean_mode in ("user_tweets", "usertweets", "user")
                else "https://api.x.com/1.1/statuses/home_timeline.json"
            )
            v1_params: Dict[str, Any] = {"count": min(max_results, 40), "tweet_mode": "extended"}
            if screen_name:
                v1_params["screen_name"] = screen_name.lstrip("@").strip()
            resp = _session().get(v1_url, headers=headers, params=v1_params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"Personal X timeline request failed: {e}",
            "platform": "x",
            "mode": mode
        }]

    results: List[Dict[str, Any]] = []

    # Handle v1.1 array of status dicts if returned by fallback
    if isinstance(data, list):
        for item in data[:max_results]:
            if not isinstance(item, dict):
                continue
            user_obj = item.get("user") if isinstance(item.get("user"), dict) else {}
            sname = user_obj.get("screen_name") or (screen_name.lstrip("@") if screen_name else "unknown")
            tid = str(item.get("id_str") or item.get("id", ""))
            post = SocialPost(
                platform="x",
                post_id=tid,
                author=f"@{sname}",
                content=item.get("full_text") or item.get("text", ""),
                timestamp=item.get("created_at", ""),
                url=f"https://x.com/{sname}/status/{tid}",
                metrics={
                    "likes": item.get("favorite_count", 0),
                    "retweets": item.get("retweet_count", 0),
                    "replies": item.get("reply_count", 0),
                },
                extra={"source_mode": mode, "api": "v1.1"}
            )
            results.append(post.to_dict())
        return results[:max_results]

    # Recursively extract tweet results from GraphQL timeline instructions
    def _walk_graphql(node: Any):
        if len(results) >= max_results:
            return
        if isinstance(node, dict):
            if "legacy" in node and ("rest_id" in node or "core" in node):
                legacy = node.get("legacy", {})
                if isinstance(legacy, dict) and ("full_text" in legacy or "text" in legacy):
                    user_legacy = (
                        node.get("core", {})
                        .get("user_results", {})
                        .get("result", {})
                        .get("legacy", {})
                    )
                    sname = user_legacy.get("screen_name") or legacy.get("user_id_str", "unknown")
                    tid = str(node.get("rest_id") or legacy.get("id_str", ""))
                    post = SocialPost(
                        platform="x",
                        post_id=tid,
                        author=f"@{sname}",
                        content=legacy.get("full_text") or legacy.get("text", ""),
                        timestamp=legacy.get("created_at", ""),
                        url=f"https://x.com/{sname}/status/{tid}",
                        metrics={
                            "likes": legacy.get("favorite_count", 0),
                            "retweets": legacy.get("retweet_count", 0),
                            "replies": legacy.get("reply_count", 0),
                        },
                        extra={"source_mode": mode}
                    )
                    results.append(post.to_dict())
                    return
            for v in node.values():
                _walk_graphql(v)
        elif isinstance(node, list):
            for item in node:
                _walk_graphql(item)

    _walk_graphql(data)
    return results[:max_results]


def fetch_personal_instagram_feed(
    username: Optional[str] = None,
    sessionid: Optional[str] = None,
    max_posts: int = 10
) -> List[Dict[str, Any]]:
    """
    Read a profile, personal saved posts, or home timeline feed on Instagram using an
    authenticated sessionid cookie (IG_SESSIONID).

    Args:
        username: Target Instagram username, 'saved' (or None) for saved posts, or 'feed'/'timeline' for home feed.
        sessionid: Value of 'sessionid' cookie from instagram.com (or IG_SESSIONID env var).
        max_posts: Maximum posts to return.
    """
    max_posts = max(0, max_posts)
    if max_posts == 0:
        return []

    resolved_sid = _clean_env(sessionid) or _clean_env(os.getenv("IG_SESSIONID"))
    if not resolved_sid:
        return [{
            "error": "Missing Personal Instagram Cookie (IG_SESSIONID)",
            "guidance": (
                "To read your personal Instagram account (Saved posts, followed private accounts, feed):\n"
                "1. Log into https://www.instagram.com in your browser.\n"
                "2. Open DevTools (F12) -> Application -> Cookies -> https://www.instagram.com.\n"
                "3. Copy the 'sessionid' cookie value and set IG_SESSIONID in your .env file "
                "(or use a logged-in WSL Chrome profile via chrome-devtools-mcp)."
            ),
            "platform": "instagram"
        }]

    headers = {
        "Cookie": f"sessionid={resolved_sid}",
        "x-ig-app-id": "936619743392459",
        "Accept": "application/json",
        "Referer": "https://www.instagram.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    clean_user = (username or "").lstrip("@").strip()
    if not clean_user or clean_user.lower() == "saved":
        endpoint = "https://www.instagram.com/api/v1/feed/saved/posts/"
        params: Dict[str, Any] = {}
    elif clean_user.lower() in ("feed", "timeline"):
        endpoint = "https://www.instagram.com/api/v1/feed/timeline/"
        params = {}
    else:
        endpoint = "https://www.instagram.com/api/v1/users/web_profile_info/"
        params = {"username": clean_user}

    try:
        resp = _session().get(endpoint, headers=headers, params=params, timeout=15)
        if resp.status_code in (401, 403):
            return [{
                "error": f"Instagram Session Authentication Failed (HTTP {resp.status_code})",
                "guidance": "Verify that IG_SESSIONID is active and comes from the same residential network.",
                "platform": "instagram"
            }]
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{
            "error": f"Personal Instagram request failed: {e}",
            "platform": "instagram"
        }]

    results: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return results

    # Parse web_profile_info edges
    edges = (
        data.get("data", {})
        .get("user", {})
        .get("edge_owner_to_timeline_media", {})
        .get("edges", [])
    )
    if isinstance(edges, list) and edges:
        for edge in edges[:max_posts]:
            node = edge.get("node", {}) if isinstance(edge, dict) else {}
            if not isinstance(node, dict):
                continue
            shortcode = node.get("shortcode", "")
            caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            caption = (
                caption_edges[0].get("node", {}).get("text", "")
                if caption_edges and isinstance(caption_edges[0], dict)
                else ""
            )
            post = SocialPost(
                platform="instagram",
                post_id=str(node.get("id", shortcode)),
                author=f"@{clean_user or 'instagram'}",
                content=caption,
                timestamp=str(node.get("taken_at_timestamp", "")),
                url=f"https://www.instagram.com/p/{shortcode}/" if shortcode else "https://www.instagram.com/",
                metrics={
                    "likes": node.get("edge_liked_by", {}).get("count", 0),
                    "comments": node.get("edge_media_to_comment", {}).get("count", 0),
                }
            )
            results.append(post.to_dict())
        return results

    # Parse saved/feed items (including timeline feed_items)
    items = data.get("items") or data.get("feed_items") or []
    if isinstance(items, list):
        for raw_it in items[:max_posts]:
            if not isinstance(raw_it, dict):
                continue
            media = raw_it.get("media") or raw_it.get("media_or_ad") or raw_it
            if not isinstance(media, dict):
                continue
            code = media.get("code", "")
            cap_obj = media.get("caption") or {}
            cap_text = cap_obj.get("text", "") if isinstance(cap_obj, dict) else ""
            user_obj = media.get("user") or {}
            uname = user_obj.get("username", clean_user or "unknown") if isinstance(user_obj, dict) else "unknown"
            post = SocialPost(
                platform="instagram",
                post_id=str(media.get("pk") or media.get("id") or code),
                author=f"@{uname}",
                content=cap_text,
                timestamp=str(media.get("taken_at", "")),
                url=f"https://www.instagram.com/p/{code}/" if code else "https://www.instagram.com/",
                metrics={
                    "likes": media.get("like_count", 0),
                    "comments": media.get("comment_count", 0),
                }
            )
            results.append(post.to_dict())

    return results


# ==============================================================================
# 5. High-Level Unified Contract (PROJECT.md)
# ==============================================================================
def search_social(
    query: str,
    platform: str = "x",
    method: str = "auto",
    max_results: int = 10,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Unified multi-platform social search contract.

    Args:
        query: Search keywords, hashtags, username, or 'bookmarks'/'saved'/'timeline'.
        platform: 'x', 'instagram', 'facebook'
        method: 'auto', 'socialdata', 'exa', 'apify', 'personal'
        max_results: Number of posts to return.
        api_key: Optional provider API key or session cookie.

    Returns:
        List of standardized post dictionaries:
        [{"platform": str, "author": str, "content": str, "timestamp": str,
          "url": str, "metrics": Dict, "engagement": Dict}]
    """
    max_results = max(0, max_results)
    if max_results == 0:
        return []

    plat = platform.lower().strip()
    meth = method.lower().strip() if method else "auto"
    q_clean = (query or "").strip().lower()

    # 1. Explicit Exa semantic search across any platform
    if meth == "exa":
        if plat in ("x", "twitter"):
            domains = ["x.com"]
        elif plat == "instagram":
            domains = ["instagram.com"]
        elif plat == "facebook":
            domains = ["facebook.com"]
        else:
            domains = ["x.com", "instagram.com", "facebook.com"]
        return search_exa_social(query, include_domains=domains, api_key=api_key, num_results=max_results)

    # 2. Personal authenticated session mode (explicit or auto-detected via reserved keywords)
    if meth in ("personal", "cookie", "bookmarks", "timeline", "user_tweets"):
        if plat in ("x", "twitter"):
            mode = (
                meth
                if meth in ("bookmarks", "timeline", "user_tweets")
                else (q_clean if q_clean in ("bookmarks", "timeline", "user_tweets") else "bookmarks")
            )
            sname = query if mode == "user_tweets" and q_clean != "user_tweets" else None
            return fetch_personal_x_timeline(
                mode=mode, auth_token=api_key, max_results=max_results, screen_name=sname
            )
        elif plat == "instagram":
            return fetch_personal_instagram_feed(username=query, sessionid=api_key, max_posts=max_results)

    # Auto-route reserved personal account keywords when method='auto'
    if meth in ("auto", ""):
        if plat in ("x", "twitter") and q_clean in ("bookmarks", "timeline"):
            return fetch_personal_x_timeline(mode=q_clean, auth_token=api_key, max_results=max_results)
        if plat == "instagram" and q_clean in ("saved", "feed", "timeline"):
            return fetch_personal_instagram_feed(username=q_clean, sessionid=api_key, max_posts=max_results)

    # 3. Platform-aware routing for 'auto', 'socialdata', 'apify'
    if plat == "instagram":
        if meth in ("auto", "apify", "socialdata", ""):
            return fetch_apify_instagram(query, api_key=api_key, max_posts=max_results)
    elif plat == "facebook":
        if meth in ("auto", "apify", "socialdata", ""):
            return fetch_apify_facebook(query, api_key=api_key, max_posts=max_results)
    elif plat in ("x", "twitter"):
        if meth in ("auto", "socialdata", ""):
            return search_socialdata_tweets(query, api_key=api_key, max_results=max_results)

    return [{
        "error": f"Unsupported platform/method combination: platform='{platform}', method='{method}'",
        "platform": platform
    }]


# ==============================================================================
# 6. Model Context Protocol (MCP) & CLI Runner
# ==============================================================================
def run_mcp_server():
    """Robust stdio Model Context Protocol (MCP) server for social data extraction."""
    tools_def = [
        {
            "name": "search_social",
            "description": "Search social media posts across X.com, Instagram, and Facebook using managed APIs or personal session cookies.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords, handle, or 'bookmarks'/'saved'"},
                    "platform": {"type": "string", "enum": ["x", "instagram", "facebook"], "default": "x"},
                    "method": {
                        "type": "string",
                        "enum": ["auto", "socialdata", "exa", "apify", "personal"],
                        "default": "auto"
                    },
                    "max_results": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        },
        {
            "name": "search_socialdata_tweets",
            "description": "Direct search on X.com via SocialData.tools with advanced operators and lang:he support.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Twitter query string"},
                    "max_results": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        },
        {
            "name": "fetch_personal_x_timeline",
            "description": "Read personal X.com Bookmarks or Home Timeline using X_AUTH_TOKEN and X_CT0 session cookies.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["bookmarks", "timeline"], "default": "bookmarks"},
                    "max_results": {"type": "integer", "default": 10}
                }
            }
        },
        {
            "name": "fetch_personal_instagram_feed",
            "description": "Read personal Instagram saved posts or profile feed using IG_SESSIONID cookie.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram handle or 'saved'"},
                    "max_posts": {"type": "integer", "default": 10}
                }
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
                        "serverInfo": {"name": "social-data-server", "version": "1.1.0"}
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
                if name == "search_social":
                    out = search_social(
                        query=str(args.get("query", "")),
                        platform=str(args.get("platform", "x")),
                        method=str(args.get("method", "auto")),
                        max_results=safe_int(args.get("max_results"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "search_socialdata_tweets":
                    out = search_socialdata_tweets(
                        query=str(args.get("query", "")),
                        max_results=safe_int(args.get("max_results"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "fetch_personal_x_timeline":
                    out = fetch_personal_x_timeline(
                        mode=str(args.get("mode", "bookmarks")),
                        max_results=safe_int(args.get("max_results"), 10)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "fetch_personal_instagram_feed":
                    out = fetch_personal_instagram_feed(
                        username=args.get("username"),
                        max_posts=safe_int(args.get("max_posts"), 10)
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
        parser = argparse.ArgumentParser(description="Social Media Search Client")
        parser.add_argument("--query", "-q", type=str, default="בינה מלאכותית", help="Search query")
        parser.add_argument("--platform", "-p", type=str, default="x", choices=["x", "instagram", "facebook"])
        parser.add_argument(
            "--method", "-m",
            type=str,
            default="auto",
            choices=["auto", "socialdata", "exa", "apify", "personal"]
        )
        parser.add_argument("--limit", "-l", type=int, default=3, help="Max results")
        args = parser.parse_args()

        results = search_social(
            query=args.query,
            platform=args.platform,
            method=args.method,
            max_results=args.limit
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
