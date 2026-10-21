"""
book_tool.py - Production Book Search & Retrieval Client for AI Agents.

Supports:
- OpenLibrary (global metadata, works, editions, IA identifiers)
- Google Books API (catalog search, volumeInfo, quota handling)
- HebrewBooks.org (rabbinic catalog search via JSONP, PDF downloads)
- Internet Archive (OCR plain-text retrieval & CDL status checking)

Provides:
- Standalone programmatic Python functions
- Unified `search_books` and `fetch_book_text` interface contracts
- CLI and stdio Model Context Protocol (MCP) server support
"""

import os
import sys
import json
import re
import time
import urllib.parse
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, asdict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (AgentDataSources/1.0)"
)


@dataclass
class BookItem:
    """Standardized book metadata representation."""
    title: str
    author: str
    year: Optional[int]
    id: str
    source: str
    has_fulltext: bool = False
    download_url: Optional[str] = None
    preview_url: Optional[str] = None
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_resilient_session(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (500, 502, 503, 504),
    user_agent: str = DEFAULT_USER_AGENT
) -> requests.Session:
    """Creates a requests.Session with connection pooling and exponential backoff."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST", "HEAD"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })
    return session


# Global shared session
_SESSION: Optional[requests.Session] = None


def _session() -> requests.Session:
    """Primary session getter with lazy initialization."""
    global _SESSION
    if _SESSION is None:
        _SESSION = get_resilient_session()
    return _SESSION


def _get_session() -> requests.Session:
    """Backward compatibility alias for _session()."""
    return _session()


def safe_int(val: Any, default: int) -> int:
    """Safely cast value to integer, falling back to default if invalid."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_jsonp(text: str) -> Any:
    """Parses JSONP wrapper, e.g. 'bot({...});' into native Python object."""
    stripped = text.strip()
    if not stripped:
        return {"data": []}
    match = re.search(r"^\s*[\w$]+\s*\((.*)\)\s*;?\s*$", stripped, re.DOTALL)
    if match:
        content = match.group(1).strip()
        if not content:
            return {"data": []}
        return json.loads(content)
    return json.loads(stripped)


# ==============================================================================
# 1. OpenLibrary Implementation
# ==============================================================================
def search_openlibrary(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search OpenLibrary REST API for books.

    Args:
        query: Keywords, title, author, or ISBN.
        limit: Maximum number of results (default 5).

    Returns:
        List of dictionaries with book metadata.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    session = _session()
    url = "https://openlibrary.org/search.json"
    params = {
        "q": query,
        "limit": limit,
        "fields": "key,title,author_name,first_publish_year,isbn,ia,has_fulltext,ebook_access"
    }

    try:
        resp = session.get(url, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return [{"error": f"OpenLibrary request failed: {e}", "source": "openlibrary"}]

    if not isinstance(data, dict):
        data = {}

    results: List[Dict[str, Any]] = []
    docs = data.get("docs", [])
    if not isinstance(docs, list):
        docs = []

    for doc in docs[:limit]:
        authors = doc.get("author_name", [])
        author_str = ", ".join(authors) if authors else "Unknown Author"
        raw_ia = doc.get("ia", [])
        ia_ids = [x for x in raw_ia if isinstance(x, str)] if isinstance(raw_ia, list) else []
        primary_ia = next((x for x in ia_ids if not x.endswith("goog")), ia_ids[0]) if ia_ids else None
        key = doc.get("key", "").replace("/works/", "")
        has_ft = doc.get("has_fulltext", False) or doc.get("ebook_access") == "public"

        preview_url = f"https://openlibrary.org{doc.get('key')}" if doc.get("key") else None
        download_url = f"https://archive.org/download/{primary_ia}/{primary_ia}_djvu.txt" if primary_ia else None

        results.append({
            "title": doc.get("title", "Untitled"),
            "author": author_str,
            "year": doc.get("first_publish_year"),
            "id": key,
            "openlibrary_key": doc.get("key"),
            "ia_identifiers": ia_ids,
            "primary_ia": primary_ia,
            "source": "openlibrary",
            "has_fulltext": has_ft,
            "download_url": download_url,
            "preview_url": preview_url,
        })

    return results


# ==============================================================================
# 2. Google Books API Implementation
# ==============================================================================
def search_google_books(
    query: str,
    api_key: Optional[str] = None,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Search Google Books API with robust quota and error guidance.

    Args:
        query: Full search query (supports 'intitle:', 'inauthor:', 'isbn:').
        api_key: Google Books API Key. Defaults to GOOGLE_BOOKS_API_KEY env var.
        limit: Max results to return (max 40).

    Returns:
        List of standardized book dictionaries or error guidance on 429 quota exhaustion.
    """
    if limit <= 0:
        return []

    session = _session()
    raw_key = api_key or os.getenv("GOOGLE_BOOKS_API_KEY")
    resolved_key = raw_key.strip() if raw_key and not raw_key.strip().lower().startswith("your_") else None

    url = "https://www.googleapis.com/books/v1/volumes"
    params: Dict[str, Any] = {
        "q": query,
        "maxResults": min(max(1, limit), 40),
        "projection": "full",
    }
    if resolved_key:
        params["key"] = resolved_key

    data: Dict[str, Any] = {}
    try:
        resp = session.get(url, params=params, timeout=12)
        if resp.status_code == 429:
            return [{
                "error": "Google Books API Quota Exceeded (HTTP 429)",
                "guidance": (
                    "Unauthenticated Google Books requests share a heavily throttled global consumer quota. "
                    "Set GOOGLE_BOOKS_API_KEY in your environment or supply api_key to obtain 1,000 requests/day."
                ),
                "source": "googlebooks",
                "quota_exhausted": True
            }]
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 429:
            return [{
                "error": "Google Books API Quota Exceeded (HTTP 429)",
                "guidance": "Please configure a valid GOOGLE_BOOKS_API_KEY from Google Cloud Console.",
                "source": "googlebooks",
                "quota_exhausted": True
            }]
        return [{
            "error": f"Google Books request failed: {e}",
            "source": "googlebooks"
        }]

    results: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        data = {}

    items = data.get("items", [])
    if not isinstance(items, list):
        items = []

    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        v_info = item.get("volumeInfo", {})
        a_info = item.get("accessInfo", {})
        s_info = item.get("searchInfo", {})

        authors = v_info.get("authors", [])
        author_str = ", ".join(authors) if authors else "Unknown Author"

        year: Optional[int] = None
        published_date = v_info.get("publishedDate")
        if published_date:
            match = re.match(r"^(\d{4})", published_date)
            if match:
                year = int(match.group(1))

        is_public = a_info.get("publicDomain", False)
        viewability = a_info.get("viewability", "")
        has_ft = is_public or viewability == "ALL_PAGES"

        pdf_info = a_info.get("pdf", {})
        epub_info = a_info.get("epub", {})
        download_url = pdf_info.get("downloadLink") or epub_info.get("downloadLink")

        results.append({
            "title": v_info.get("title", "Untitled"),
            "author": author_str,
            "year": year,
            "id": item.get("id"),
            "source": "googlebooks",
            "has_fulltext": has_ft,
            "download_url": download_url,
            "preview_url": v_info.get("previewLink") or v_info.get("infoLink"),
            "snippet": s_info.get("textSnippet"),
            "description": v_info.get("description"),
            "viewability": viewability,
            "is_public_domain": is_public
        })

    return results


# ==============================================================================
# 3. HebrewBooks.org & Sefaria Implementation
# ==============================================================================
def suggest_hebrewbooks(query: str, limit: int = 10) -> List[str]:
    """
    Query HebrewBooks.org autocomplete suggest endpoint (suggest.ashx).
    """
    limit = max(0, limit)
    if limit == 0 or not query.strip():
        return []

    url = "https://beta.hebrewbooks.org/suggest/suggest.ashx"
    params = {
        "json": 1,
        "autosuggest": 1,
        "limit": limit,
        "src": "title",
        "q": query.strip()
    }
    try:
        resp = _session().get(url, params=params, timeout=12)
        resp.raise_for_status()
        items = resp.json()
        if isinstance(items, list):
            return [str(x).replace("\r", "").strip() for x in items[:limit] if str(x).strip()]
    except Exception:
        pass
    return []


def fetch_hebrewbooks_shas(
    mesechta: int = 1,
    daf: Union[int, str] = 2,
    max_chars: int = 10000
) -> Dict[str, Any]:
    """
    Fetch plain-text Talmud Bavli (Gemara, Rashi, Tosafot) from HebrewBooks shas.aspx.

    Args:
        mesechta: Tractate number (1=Berakhot, 2=Shabbat, etc.).
        daf: Page identifier (e.g. 2 for 2a, '2b' for 2b).
        max_chars: Maximum characters to return.
    """
    url = "https://hebrewbooks.org/shas.aspx"
    params = {"mesechta": mesechta, "daf": daf, "format": "text"}
    try:
        resp = _session().get(url, params=params, timeout=15)
        resp.raise_for_status()
        html_text = resp.text
    except Exception as e:
        return {
            "id": f"shas_{mesechta}_{daf}",
            "source": "hebrewbooks_shas",
            "status": "error",
            "error": f"Failed fetching HebrewBooks Shas text: {e}",
            "text": None
        }

    def _extract_div(cls_name: str) -> str:
        m = re.search(rf'class="{cls_name}"[^>]*>(.*?)</div>', html_text, re.DOTALL | re.IGNORECASE)
        if not m:
            return ""
        cleaned = re.sub(r"<[^>]+>", " ", m.group(1))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    gemara = _extract_div("shastext2")
    rashi = _extract_div("shastext3")
    tosafot = _extract_div("shastext4")

    combined_parts = []
    if gemara:
        combined_parts.append(f"[גמרא]\n{gemara}")
    if rashi:
        combined_parts.append(f"[רש\"י]\n{rashi}")
    if tosafot:
        combined_parts.append(f"[תוספות]\n{tosafot}")
    combined = "\n\n".join(combined_parts)

    return {
        "id": f"shas_{mesechta}_{daf}",
        "source": "hebrewbooks_shas",
        "status": "success" if combined else "empty",
        "mesechta": mesechta,
        "daf": str(daf),
        "gemara": gemara[:max_chars],
        "rashi": rashi[:max_chars],
        "tosafot": tosafot[:max_chars],
        "text": combined[:max_chars]
    }


def search_hebrewbooks(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search HebrewBooks.org catalog using the beta JSONP API.

    Args:
        query: Hebrew title or author substring.
        limit: Number of items to retrieve (default 5).

    Returns:
        List of dictionaries with book ID, title, author, and download link.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    session = _session()
    url = "https://beta.hebrewbooks.org/api/api.ashx"
    params = {
        "title_search": query,
        "start": 0,
        "length": limit,
        "callback": "bot"
    }

    try:
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        raw_text = resp.text.strip()
        if not raw_text or raw_text in ("bot();", "bot( );", "bot()", "bot( )"):
            return []
        parsed = _parse_jsonp(raw_text)
    except Exception as e:
        return [{"error": f"HebrewBooks request failed: {e}", "source": "hebrewbooks"}]

    raw_items = parsed.get("data", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_items, list):
        raw_items = []
    results: List[Dict[str, Any]] = []

    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue
        b_id = str(item.get("id", ""))
        title = item.get("title", "ללא כותרת")
        author = item.get("author", "מחבר לא ידוע")

        # Fallback to extract author from title if separated by dash
        if author == "מחבר לא ידוע" and " - " in title:
            parts = title.split(" - ")
            title_part = parts[0].strip()
            author_part = parts[1].strip()
            title = title_part
            author = author_part

        results.append({
            "title": title,
            "author": author,
            "year": None,
            "id": b_id,
            "source": "hebrewbooks",
            "has_fulltext": True,  # Scanned PDF always available for every ID
            "download_url": f"https://download.hebrewbooks.org/downloadhandler.ashx?req={b_id}",
            "preview_url": f"https://beta.hebrewbooks.org/{b_id}",
        })

    return results


def download_hebrewbooks_pdf(
    book_id: Union[int, str],
    dest_path: str,
    max_bytes: Optional[int] = None
) -> str:
    """
    Download complete PDF from HebrewBooks.org.

    Args:
        book_id: Numeric book ID (e.g. 16102).
        dest_path: File system path where the PDF will be written.
        max_bytes: Optional limit for testing/streaming partial downloads.

    Returns:
        The written dest_path string.
    """
    session = _session()
    url = f"https://download.hebrewbooks.org/downloadhandler.ashx?req={book_id}"

    headers = {
        "Referer": f"https://beta.hebrewbooks.org/{book_id}",
    }

    resp = session.get(url, headers=headers, stream=True, timeout=30)
    resp.raise_for_status()

    # Ensure parent directory exists
    parent_dir = os.path.dirname(os.path.abspath(dest_path))
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    bytes_written = 0
    first_chunk = True
    try:
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                if first_chunk:
                    stripped_chunk = chunk.lstrip()
                    if not chunk.startswith(b"%PDF") and not stripped_chunk.startswith(b"%PDF"):
                        raise ValueError(f"Invalid PDF stream from HebrewBooks: received {chunk[:20]!r}")
                    first_chunk = False
                f.write(chunk)
                bytes_written += len(chunk)
                if max_bytes and bytes_written >= max_bytes:
                    break

        if first_chunk:
            raise ValueError(f"Empty response received from HebrewBooks for book ID {book_id}")
    except Exception:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        raise

    return dest_path


def search_sefaria(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search Sefaria.org digital library via reference resolution (/api/name) and
    full-text ElasticSearch (/api/search-wrapper).
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    session = _session()
    results: List[Dict[str, Any]] = []

    # 1. Check direct reference / title resolution via /api/name/{query}
    try:
        name_url = f"https://www.sefaria.org/api/name/{urllib.parse.quote(query.strip())}"
        name_resp = session.get(name_url, timeout=10)
        if name_resp.status_code == 200:
            name_data = name_resp.json()
            if isinstance(name_data, dict) and name_data.get("is_ref") and name_data.get("ref"):
                ref = str(name_data["ref"])
                encoded_ref = urllib.parse.quote(ref)
                results.append({
                    "title": ref,
                    "author": "Sefaria Canonical Text",
                    "year": None,
                    "id": ref,
                    "source": "sefaria",
                    "has_fulltext": True,
                    "download_url": f"https://www.sefaria.org/api/texts/{encoded_ref}?context=0",
                    "preview_url": f"https://www.sefaria.org/{encoded_ref}",
                })
    except Exception:
        pass

    if len(results) >= limit:
        return results[:limit]

    # 2. Full-text search via POST /api/search-wrapper
    search_url = "https://www.sefaria.org/api/search-wrapper"
    payload = {
        "query": query,
        "type": "text",
        "size": max(1, limit - len(results)),
    }
    try:
        resp = session.post(search_url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        if results:
            return results[:limit]
        return [{"error": f"Sefaria search request failed: {e}", "source": "sefaria"}]

    hits_outer = data.get("hits", {}) if isinstance(data, dict) else {}
    hits = hits_outer.get("hits", []) if isinstance(hits_outer, dict) else []
    if not isinstance(hits, list):
        hits = []

    for hit in hits:
        if not isinstance(hit, dict) or len(results) >= limit:
            continue
        raw_id = str(hit.get("_id", ""))
        ref = raw_id.split(" (")[0].strip() if " (" in raw_id else raw_id
        encoded_ref = urllib.parse.quote(ref)
        hl = hit.get("highlight", {}) if isinstance(hit.get("highlight"), dict) else {}
        snippets = hl.get("exact") or hl.get("naive_lemmatizer") or []
        snippet_str = " ... ".join(snippets) if isinstance(snippets, list) else ""

        results.append({
            "title": ref or raw_id,
            "author": "Sefaria Corpus",
            "year": None,
            "id": ref or raw_id,
            "source": "sefaria",
            "has_fulltext": True,
            "download_url": f"https://www.sefaria.org/api/texts/{encoded_ref}?context=0",
            "preview_url": f"https://www.sefaria.org/{encoded_ref}",
            "snippet": snippet_str,
        })

    return results[:limit]


def fetch_sefaria_text(ref: str, max_chars: int = 10000) -> Dict[str, Any]:
    """
    Fetch full Hebrew and English text for a Sefaria reference via /api/texts/{ref}?context=0.
    """
    session = _session()
    clean_ref = ref.strip()

    def _flatten_text(val: Any) -> str:
        if isinstance(val, str):
            return re.sub(r"<[^>]+>", "", val).strip()
        if isinstance(val, list):
            parts = [_flatten_text(x) for x in val]
            return "\n".join(p for p in parts if p)
        return ""

    url = f"https://www.sefaria.org/api/texts/{urllib.parse.quote(clean_ref)}?context=0"
    try:
        resp = session.get(url, timeout=12)
        if resp.status_code == 404:
            # Attempt resolving via /api/name/{ref}
            name_resp = session.get(f"https://www.sefaria.org/api/name/{urllib.parse.quote(clean_ref)}", timeout=10)
            if name_resp.status_code == 200:
                nd = name_resp.json()
                if isinstance(nd, dict) and nd.get("ref"):
                    clean_ref = str(nd["ref"])
                    resp = session.get(
                        f"https://www.sefaria.org/api/texts/{urllib.parse.quote(clean_ref)}?context=0",
                        timeout=12
                    )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {
            "id": ref,
            "source": "sefaria",
            "status": "error",
            "error": f"Failed fetching Sefaria text for '{ref}': {e}",
            "text": None
        }

    if not isinstance(data, dict) or data.get("error"):
        return {
            "id": ref,
            "source": "sefaria",
            "status": "error",
            "error": data.get("error", "Invalid Sefaria response") if isinstance(data, dict) else "Invalid response",
            "text": None
        }

    he_text = _flatten_text(data.get("he", []))
    en_text = _flatten_text(data.get("text", []))
    combined = "\n\n".join(part for part in [he_text, en_text] if part)

    return {
        "id": data.get("ref", clean_ref),
        "he_ref": data.get("heRef", ""),
        "source": "sefaria",
        "status": "success" if combined else "empty",
        "title": data.get("ref", clean_ref),
        "hebrew_text": he_text[:max_chars],
        "english_text": en_text[:max_chars],
        "text": combined[:max_chars],
        "total_chars": len(combined),
        "is_truncated": len(combined) > max_chars,
    }


# ==============================================================================
# 4. Internet Archive Text & CDL Status
# ==============================================================================
def _resolve_openlibrary_ia_candidates(ol_key: str) -> List[str]:
    """
    Resolve an OpenLibrary work (OL...W) or edition (OL...M) key into a prioritized
    list of Internet Archive identifiers (preferring non-'goog' scans).
    """
    clean = ol_key.strip()
    if clean.startswith("/works/"):
        clean = clean.replace("/works/", "")
    elif clean.startswith("/books/"):
        clean = clean.replace("/books/", "")

    session = _session()
    ia_ids: List[str] = []

    def _add_work_ia(work_clean: str) -> None:
        try:
            resp = session.get(
                "https://openlibrary.org/search.json",
                params={"q": f"key:/works/{work_clean}", "fields": "key,ia", "limit": 1},
                timeout=12
            )
            if resp.status_code == 200:
                docs = resp.json().get("docs", [])
                if docs and isinstance(docs[0], dict):
                    raw_ia = docs[0].get("ia", [])
                    if isinstance(raw_ia, list):
                        for x in raw_ia:
                            if x and str(x) not in ia_ids:
                                ia_ids.append(str(x))
        except Exception:
            pass

    if clean.endswith("W"):
        _add_work_ia(clean)
    elif clean.endswith("M"):
        try:
            resp = session.get(f"https://openlibrary.org/books/{clean}.json", timeout=12)
            if resp.status_code == 200:
                bdata = resp.json()
                if isinstance(bdata, dict):
                    ocaid = bdata.get("ocaid")
                    if ocaid:
                        ia_ids.append(str(ocaid))
                    src_recs = bdata.get("source_records", [])
                    if isinstance(src_recs, list):
                        for sr in src_recs:
                            if isinstance(sr, str) and sr.startswith("ia:"):
                                cand = sr[3:].strip()
                                if cand and cand not in ia_ids:
                                    ia_ids.append(cand)
                    works = bdata.get("works", [])
                    if isinstance(works, list):
                        for w in works:
                            if isinstance(w, dict) and w.get("key"):
                                w_clean = str(w["key"]).replace("/works/", "").strip()
                                if w_clean.endswith("W"):
                                    _add_work_ia(w_clean)
        except Exception:
            pass

    non_goog = [x for x in ia_ids if not x.endswith("goog")]
    goog = [x for x in ia_ids if x.endswith("goog")]
    return non_goog + goog


def _fetch_single_ia_item_text(ia_id: str, max_chars: int = 10000) -> Dict[str, Any]:
    """Fetch public domain text or CDL status for a single Internet Archive identifier."""
    session = _session()
    meta_url = f"https://archive.org/metadata/{ia_id}"

    try:
        resp = session.get(meta_url, timeout=12)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {
            "id": ia_id,
            "status": "error",
            "error": f"Failed to fetch metadata for {ia_id}: {e}",
            "is_borrowable": False,
            "text": None
        }

    if not isinstance(data, dict):
        data = {}

    meta = data.get("metadata", {})
    if not isinstance(meta, dict):
        meta = {}
    files = data.get("files", [])
    if not isinstance(files, list):
        files = []

    is_borrowable = (
        meta.get("access-restricted-item") == "true" or
        meta.get("access-restricted-item") is True or
        "borrowable" in str(meta.get("ebook_access", "")).lower()
    )

    if is_borrowable:
        return {
            "id": ia_id,
            "status": "restricted_cdl",
            "is_borrowable": True,
            "title": meta.get("title", ""),
            "text": None,
            "message": (
                "Item is protected under Controlled Digital Lending (CDL). "
                "Direct automated OCR text extraction returns HTTP 401/403. "
                f"Borrowable via web reader at: https://archive.org/details/{ia_id}"
            )
        }

    # Find candidate text files: prefer _djvu.txt, then .txt
    txt_candidates = [
        f.get("name") for f in files
        if isinstance(f, dict) and str(f.get("name", "")).endswith("_djvu.txt")
    ]
    if not txt_candidates:
        txt_candidates = [
            f.get("name") for f in files
            if isinstance(f, dict)
            and str(f.get("name", "")).endswith(".txt")
            and not str(f.get("name", "")).endswith("_meta.txt")
            and not str(f.get("name", "")).endswith("_files.xml")
        ]

    if not txt_candidates:
        return {
            "id": ia_id,
            "status": "no_text_file",
            "is_borrowable": False,
            "title": meta.get("title", ""),
            "text": None,
            "message": "No direct plain text or _djvu.txt OCR asset found on this item manifest."
        }

    target_file = txt_candidates[0]
    download_url = f"https://archive.org/download/{ia_id}/{target_file}"

    try:
        txt_resp = session.get(download_url, timeout=15)
        if txt_resp.status_code in (401, 403):
            return {
                "id": ia_id,
                "status": "restricted_cdl",
                "is_borrowable": True,
                "text": None,
                "message": f"Asset {target_file} returned HTTP {txt_resp.status_code} (access restricted)."
            }
        txt_resp.raise_for_status()
        raw_text = txt_resp.text
        truncated = raw_text[:max_chars]
        return {
            "id": ia_id,
            "status": "success",
            "is_borrowable": False,
            "title": meta.get("title", ""),
            "file_used": target_file,
            "total_chars": len(raw_text),
            "text": truncated,
            "is_truncated": len(raw_text) > max_chars
        }
    except Exception as e:
        return {
            "id": ia_id,
            "status": "error",
            "is_borrowable": is_borrowable,
            "error": f"Failed downloading text asset {target_file}: {e}",
            "text": None
        }


def fetch_ia_text(ia_id: str, max_chars: int = 10000) -> Dict[str, Any]:
    """
    Fetch public domain text from Internet Archive or report CDL status cleanly.
    Automatically resolves OpenLibrary work/edition keys (OL...W / OL...M) to their
    candidate IA identifiers and iterates until a full-text asset is found.

    Args:
        ia_id: Internet Archive identifier or OpenLibrary work/edition key.
        max_chars: Maximum characters of text to return (default 10,000).

    Returns:
        Dictionary with status, extracted text or CDL borrowable notice.
    """
    clean_id = ia_id.strip()
    if re.match(r"^(/works/|/books/)?OL\d+[WM]$", clean_id):
        candidates = _resolve_openlibrary_ia_candidates(clean_id)
        if not candidates:
            return {
                "id": ia_id,
                "status": "no_text_file",
                "is_borrowable": False,
                "text": None,
                "message": f"No Internet Archive identifiers linked to OpenLibrary key {ia_id}."
            }
        last_res: Optional[Dict[str, Any]] = None
        for cand in candidates[:8]:
            res = _fetch_single_ia_item_text(cand, max_chars=max_chars)
            if res.get("status") == "success":
                res["openlibrary_key"] = ia_id
                return res
            if last_res is None or (res.get("status") == "restricted_cdl" and last_res.get("status") != "restricted_cdl"):
                last_res = res
        if last_res is not None:
            last_res["openlibrary_key"] = ia_id
            return last_res

    return _fetch_single_ia_item_text(clean_id, max_chars=max_chars)


# ==============================================================================
# 5. High-Level Unified Contracts (PROJECT.md)
# ==============================================================================
def search_books(
    query: str,
    source: str = "openlibrary",
    max_results: int = 5,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Standardized multi-repository book search.

    Args:
        query: Search term
        source: 'openlibrary', 'googlebooks', 'hebrewbooks', 'sefaria'
        max_results: Max items
        api_key: Optional API key for Google Books

    Returns:
        List of standardized book dicts:
        [{"title": str, "author": str, "year": Optional[int], "id": str,
          "source": str, "has_fulltext": bool, "download_url": Optional[str],
          "preview_url": Optional[str]}]
    """
    max_results = max(0, max_results)
    src = source.lower().strip()
    if src in ("openlibrary", "ol", "open_library"):
        return search_openlibrary(query, limit=max_results)
    elif src in ("googlebooks", "google_books", "google"):
        return search_google_books(query, api_key=api_key, limit=max_results)
    elif src in ("hebrewbooks", "hebrew_books", "hb"):
        return search_hebrewbooks(query, limit=max_results)
    elif src in ("sefaria", "sefaria.org"):
        return search_sefaria(query, limit=max_results)
    else:
        return [{
            "error": (
                f"Unsupported book source: '{source}'. "
                "Valid sources: 'openlibrary', 'googlebooks', 'hebrewbooks', 'sefaria'."
            ),
            "source": source
        }]


def fetch_book_text(
    book_id: str,
    source: str = "openlibrary",
    max_chars: int = 10000
) -> Dict[str, Any]:
    """
    Standardized full-text / preview extraction.

    Args:
        book_id: Repository specific book identifier, IA identifier, or Sefaria ref.
        source: 'openlibrary' (IA ID or OL key), 'hebrewbooks' (book ID or 'shas:1:2'), or 'sefaria' (ref).
        max_chars: Character limit.

    Returns:
        Dictionary with extracted text or error/borrowable details.
    """
    src = source.lower().strip()
    if src in ("openlibrary", "ia", "internet_archive"):
        return fetch_ia_text(book_id, max_chars=max_chars)
    elif src in ("sefaria", "sefaria.org"):
        return fetch_sefaria_text(book_id, max_chars=max_chars)
    elif src in ("hebrewbooks", "hb"):
        if str(book_id).startswith("shas:"):
            parts = str(book_id).split(":")
            m_id = safe_int(parts[1] if len(parts) > 1 else 1, 1)
            d_id = parts[2] if len(parts) > 2 else 2
            return fetch_hebrewbooks_shas(mesechta=m_id, daf=d_id, max_chars=max_chars)
        return {
            "id": book_id,
            "source": "hebrewbooks",
            "download_url": f"https://download.hebrewbooks.org/downloadhandler.ashx?req={book_id}",
            "reader_url": f"https://beta.hebrewbooks.org/{book_id}",
            "note": (
                "HebrewBooks provides scanned PDF assets (and structured Talmud text via fetch_hebrewbooks_shas). "
                "Download PDF and use local_books_tool / PyMuPDF for text extraction."
            )
        }
    else:
        return {"error": f"Unsupported text source: {source}", "id": book_id}


# ==============================================================================
# 6. Model Context Protocol (MCP) & CLI Runner
# ==============================================================================
def run_mcp_server():
    """Robust stdio Model Context Protocol (MCP) server for book search and text retrieval."""
    tools_def = [
        {
            "name": "search_books",
            "description": "Search across OpenLibrary, Google Books, HebrewBooks.org, and Sefaria.org catalogs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query, title, or Sefaria reference"},
                    "source": {
                        "type": "string",
                        "enum": ["openlibrary", "googlebooks", "hebrewbooks", "sefaria"],
                        "default": "openlibrary"
                    },
                    "max_results": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        },
        {
            "name": "fetch_ia_text",
            "description": "Fetch public domain OCR text from Internet Archive by IA ID or OpenLibrary Work key, or check CDL status.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ia_id": {"type": "string", "description": "Internet Archive identifier or OL...W key"},
                    "max_chars": {"type": "integer", "default": 10000}
                },
                "required": ["ia_id"]
            }
        },
        {
            "name": "fetch_book_text",
            "description": "Fetch full text from OpenLibrary/IA, Sefaria.org reference, or HebrewBooks Shas.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "string", "description": "Book ID, IA ID, Sefaria ref (e.g. 'Berakhot 2a'), or 'shas:1:2'"},
                    "source": {
                        "type": "string",
                        "enum": ["openlibrary", "sefaria", "hebrewbooks"],
                        "default": "openlibrary"
                    },
                    "max_chars": {"type": "integer", "default": 10000}
                },
                "required": ["book_id"]
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
                        "serverInfo": {"name": "books-search-server", "version": "1.1.0"}
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
                if name == "search_books":
                    out = search_books(
                        query=str(args.get("query", "")),
                        source=str(args.get("source", "openlibrary")),
                        max_results=safe_int(args.get("max_results"), 5)
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name == "fetch_ia_text":
                    out = fetch_ia_text(
                        ia_id=str(args.get("ia_id", "")),
                        max_chars=safe_int(args.get("max_chars"), 10000)
                    )
                    if isinstance(out, dict) and (out.get("status") == "error" or "error" in out):
                        is_err = True
                elif name == "fetch_book_text":
                    out = fetch_book_text(
                        book_id=str(args.get("book_id", "")),
                        source=str(args.get("source", "openlibrary")),
                        max_chars=safe_int(args.get("max_chars"), 10000)
                    )
                    if isinstance(out, dict) and (out.get("status") == "error" or "error" in out):
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
        parser = argparse.ArgumentParser(description="Book Search and Retrieval Tool")
        parser.add_argument("--query", "-q", type=str, default="talmud", help="Search query")
        parser.add_argument(
            "--source", "-s",
            type=str,
            default="openlibrary",
            choices=["openlibrary", "googlebooks", "hebrewbooks", "sefaria"]
        )
        parser.add_argument("--limit", "-l", type=int, default=3, help="Max results")
        args = parser.parse_args()

        results = search_books(query=args.query, source=args.source, max_results=args.limit)
        print(json.dumps(results, ensure_ascii=False, indent=2))
