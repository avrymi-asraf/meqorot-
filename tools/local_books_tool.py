#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pymupdf>=1.24.0",
# ]
# ///
"""
local_books_tool.py - High-Speed Local Book & PDF Library Indexer and Search Engine for AI Agents.

Features:
- Multi-format extraction: PDF (via PyMuPDF/fitz), EPUB (ZIP/XHTML), TXT, and Markdown (.md)
- Hebrew linguistic normalization:
  1. `strip_nikkud`: Removes Unicode Hebrew diacritics & cantillation marks (U+0591..U+05C7)
  2. `fix_legacy_hebrew_pdf`: Repairs legacy visual-RTL Windows-1255 (`0xE0..0xFA` in Latin-1)
     and `/CE0../CFA` glyph-encoded Hebrew PDFs into logical UTF-8 Hebrew
  3. `expand_hebrew_word_fts5`: Expands Hebrew roots across prefixes (ה, ו, ב, ל, מ, כ, ש...)
     and final-to-medial letter transitions (SOFIT_TO_MEDIAL: ך->כ, ם->מ, ן->נ, ף->פ, ץ->צ)
- Dual SQLite FTS5 indexing:
  - `book_pages_fts`: `unicode61 remove_diacritics 2` with BM25 ranking + prefix expansion
  - `book_pages_trigram`: `trigram` tokenizer for exact substring & morphology fallback
- Incremental mtime caching (only re-indexes modified files)
- Standalone Python API, CLI, and hardened stdio MCP server (`--mcp`)
"""

import glob
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


def _ensure_pymupdf_imported():
    """Import pymupdf (fitz), auto-discovering cached uv environments if needed."""
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # type: ignore
        return fitz
    except ImportError:
        pass

    # Auto-discover cached cp311-abi3 pymupdf in ~/.cache/uv
    uv_site_dirs = sorted(
        glob.glob(os.path.expanduser("~/.cache/uv/environments-v2/**/site-packages"), recursive=True)
        + glob.glob(os.path.expanduser("~/.cache/uv/archive-v0/**/site-packages"), recursive=True),
        reverse=True,
    )
    for sp in uv_site_dirs:
        if os.path.isdir(os.path.join(sp, "pymupdf")) or os.path.isdir(os.path.join(sp, "fitz")):
            if sp not in sys.path:
                sys.path.insert(0, sp)
            try:
                import pymupdf  # type: ignore
                return pymupdf
            except ImportError:
                try:
                    import fitz  # type: ignore
                    return fitz
                except ImportError:
                    continue
    return None


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_DB_PATH = os.environ.get(
    "LOCAL_BOOKS_DB_PATH",
    os.path.join(PROJECT_ROOT, ".local_books_index.db")
)

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".txt", ".md"}

SOFIT_TO_MEDIAL: Dict[str, str] = {
    "ך": "כ",
    "ם": "מ",
    "ן": "נ",
    "ף": "פ",
    "ץ": "צ",
}

HEBREW_PREFIXES: List[str] = [
    "",
    "ה", "ו", "ב", "ל", "מ", "כ", "ש",
    "וה", "וב", "ול", "ומ", "וכ", "וש",
    "שה", "שב", "של", "שמ", "שכ",
]

_LATIN1_HEBREW_TRANS = str.maketrans({
    chr(b): chr(0x05D0 + b - 0xE0) for b in range(0xE0, 0xFB)
})


def safe_int(val: Any, default: int) -> int:
    """Safely cast value to integer, falling back to default if invalid."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ==============================================================================
# 1. Hebrew Normalization, Legacy PDF Repair & FTS5 Query Expansion
# ==============================================================================
def strip_nikkud(text: str) -> str:
    """
    Normalize Unicode to NFC, strip Hebrew diacritics (nikkud) and cantillation marks
    (U+0591..U+05C7), remove stray control chars, and normalize Hebrew punctuation.
    """
    if not text:
        return ""
    norm = unicodedata.normalize("NFC", text)
    # Remove PDF control characters such as \x1e (record separator)
    norm = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", norm)
    # Strip Hebrew combining vowels and cantillation marks
    norm = re.sub(r"[\u0591-\u05C7]", "", norm)
    # Normalize Hebrew punctuation
    norm = norm.replace("\u05BE", "-").replace("\u05F4", '"').replace("\u05F3", "'")
    return norm


def fix_legacy_hebrew_pdf(text: str) -> str:
    """
    Detect and repair legacy Hebrew encodings in PDF text layers:
    1. PostScript `/CE0`..`/CFA` hex glyph names -> Unicode Hebrew `א`..`ת`.
    2. Visual-RTL Windows-1255 stored in Latin-1 (`0xE0..0xFA`, e.g. 'íééç' -> 'חיים'):
       transcodes `0xE0..0xFA` to `U+05D0..U+05EA` and reverses visual-RTL runs into
       logical UTF-8 order.
    """
    if not text:
        return ""

    out = text
    # 1. Replace /CE0 .. /CFA PostScript glyph sequences if present
    if "/C" in out:
        def _glyph_repl(match: re.Match) -> str:
            code = int(match.group(1), 16)
            if 0xE0 <= code <= 0xFA:
                return chr(0x05D0 + code - 0xE0)
            return match.group(0)

        out = re.sub(r"/C([E-F][0-9A-F])", _glyph_repl, out)
        # If a decoded /CE0 token starts with a Hebrew final letter (ךםןףץ), it was stored visually RTL
        if re.search(r"/C[E-F][0-9A-F]", text):
            out = re.sub(
                r"[\u05D0-\u05EA\"'״׳]+",
                lambda m: m.group(0)[::-1] if m.group(0)[0] in "ךםןףץ" else m.group(0),
                out,
            )

    # 2. Check for Latin-1 0xE0..0xFA Hebrew characters (when no standard Unicode Hebrew is present
    #    or when Latin-1 high bytes dominate)
    latin_heb_count = sum(1 for c in out if "\u00e0" <= c <= "\u00fa")
    unicode_heb_count = sum(1 for c in out if "\u05d0" <= c <= "\u05ea")
    if latin_heb_count >= 3 and latin_heb_count > unicode_heb_count:
        transcoded = out.translate(_LATIN1_HEBREW_TRANS)
        # Reverse visual-RTL Hebrew word runs into logical order
        out = re.sub(r"[\u05D0-\u05EA\"'״׳]+", lambda m: m.group(0)[::-1], transcoded)

    return out


def expand_hebrew_word_fts5(word: str) -> str:
    """
    Expand a single word into an FTS5 disjunctive prefix expression handling:
    - Hebrew abbreviations with internal gershayim (e.g. בע"מ, דו"ח, רמב"ם, עפ"י)
      matching both split FTS5 phrase tokens ("בע מ"), visual-RTL quote-inverted tokens ("מ בע"),
      and unquoted exact tokens ("בעמ") without false-positive prefix matches.
    - Common Hebrew proclitic prefixes (ה, ו, ב, ל, מ, כ, ש, וה, וב, שה...)
    - Final-to-medial consonant transitions (ך->כ, ם->מ, ן->נ, ף->פ, ץ->צ) so roots
      ending in a final letter match suffixed forms (e.g. מלך -> מלכים, ירושלים -> בירושלים).
    """
    clean_norm = strip_nikkud(word).strip()
    abbr_match = re.match(r'^([\u05D0-\u05EA]+)"([\u05D0-\u05EA]+)$', clean_norm)
    if abbr_match:
        p1, p2 = abbr_match.group(1), abbr_match.group(2)
        abbr_variants: List[str] = []
        seen_abbr = set()
        for pref in ["", "ה", "ו", "ב", "ל", "מ", "כ", "ש"]:
            for cand in (f'"{pref}{p1} {p2}"', f'"{p2} {pref}{p1}"', f'"{pref}{p1}{p2}"'):
                if cand not in seen_abbr:
                    seen_abbr.add(cand)
                    abbr_variants.append(cand)
        return "(" + " OR ".join(abbr_variants) + ")"

    clean = re.sub(r'[^\w\u05D0-\u05EA]', "", clean_norm)
    if not clean:
        return ""

    # Non-Hebrew token: use simple prefix match
    if not re.search(r"[\u05D0-\u05EA]", clean):
        return f'"{clean}"*'

    stems = [clean]
    if clean[-1] in SOFIT_TO_MEDIAL and len(clean) >= 2:
        medial_stem = clean[:-1] + SOFIT_TO_MEDIAL[clean[-1]]
        if medial_stem not in stems:
            stems.append(medial_stem)

    # Also allow stripping a leading Hebrew prefix if the query word itself starts with one (len >= 4)
    if len(clean) >= 4 and clean[0] in ("ה", "ו", "ב", "ל", "מ", "כ", "ש"):
        bare = clean[1:]
        if bare not in stems:
            stems.append(bare)
            if bare[-1] in SOFIT_TO_MEDIAL and len(bare) >= 2:
                bare_medial = bare[:-1] + SOFIT_TO_MEDIAL[bare[-1]]
                if bare_medial not in stems:
                    stems.append(bare_medial)

    variants: List[str] = []
    seen = set()
    for st in stems:
        for pref in HEBREW_PREFIXES:
            cand = f'"{pref}{st}"*'
            if cand not in seen:
                seen.add(cand)
                variants.append(cand)

    return "(" + " OR ".join(variants) + ")"


def build_fts5_query(query: str, expand_hebrew: bool = True) -> str:
    """Build a complete SQLite FTS5 query string from a multi-word user query."""
    clean_q = strip_nikkud(query).strip()
    tokens = [t for t in re.split(r"\s+", clean_q) if re.sub(r'[^\w\u05D0-\u05EA]', "", t)]
    if not tokens:
        return ""

    parts: List[str] = []
    for tok in tokens:
        if expand_hebrew:
            expanded = expand_hebrew_word_fts5(tok)
            if expanded:
                parts.append(expanded)
        else:
            w = re.sub(r'[^\w\u05D0-\u05EA]', "", tok)
            if w:
                parts.append(f'"{w}"*')

    return " AND ".join(parts)


# ==============================================================================
# 2. Multi-Format Document Extraction (PDF, EPUB, TXT, MD)
# ==============================================================================
def extract_document_pages(
    file_path: str,
    max_pages: int = 1000
) -> List[Tuple[int, str, str]]:
    """
    Extract (page_num, raw_text, clean_text) tuples from a PDF, EPUB, TXT, or MD file.
    """
    ext = os.path.splitext(file_path)[1].lower()
    pages_out: List[Tuple[int, str, str]] = []

    if ext == ".pdf":
        fitz_mod = _ensure_pymupdf_imported()
        if fitz_mod is None:
            raise RuntimeError("PyMuPDF (pymupdf/fitz) is required to extract PDF files.")
        doc = fitz_mod.open(file_path)
        try:
            if doc.needs_pass:
                return []
            total = min(doc.page_count, max_pages)
            for idx in range(total):
                page = doc.load_page(idx)
                raw = page.get_text("text") or ""
                fixed = fix_legacy_hebrew_pdf(raw)
                cleaned = strip_nikkud(fixed)
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                if cleaned:
                    pages_out.append((idx + 1, fixed.strip(), cleaned))
        finally:
            doc.close()

    elif ext == ".epub":
        with zipfile.ZipFile(file_path, "r") as zf:
            html_names = sorted(
                n for n in zf.namelist()
                if n.lower().endswith((".xhtml", ".html", ".htm"))
            )
            for idx, name in enumerate(html_names[:max_pages]):
                raw_bytes = zf.read(name)
                text_html = raw_bytes.decode("utf-8", errors="replace")
                plain = html.unescape(re.sub(r"<[^>]+>", " ", text_html))
                fixed = fix_legacy_hebrew_pdf(plain)
                cleaned = strip_nikkud(fixed)
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                if cleaned:
                    pages_out.append((idx + 1, fixed.strip(), cleaned))

    elif ext in (".txt", ".md"):
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        try:
            text_all = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_all = raw_bytes.decode("windows-1255", errors="replace")

        # Chunk plain text into ~3000-character virtual pages
        chunk_size = 3000
        for idx in range(0, min(len(text_all), chunk_size * max_pages), chunk_size):
            chunk = text_all[idx: idx + chunk_size]
            fixed = fix_legacy_hebrew_pdf(chunk)
            cleaned = strip_nikkud(fixed)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                pages_out.append(((idx // chunk_size) + 1, fixed.strip(), cleaned))

    return pages_out


# ==============================================================================
# 3. SQLite FTS5 Indexer & Search Engine
# ==============================================================================
def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            book_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            page_count INTEGER NOT NULL,
            indexed_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS book_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL,
            page_num INTEGER NOT NULL,
            raw_text TEXT NOT NULL,
            clean_text TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(book_id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS book_pages_fts USING fts5(
            title,
            clean_text,
            content='book_pages',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS book_pages_trigram USING fts5(
            title,
            clean_text,
            content='book_pages',
            content_rowid='id',
            tokenize='trigram'
        );
        """
    )


def _discover_files(paths: Union[str, List[str]]) -> List[str]:
    if isinstance(paths, str):
        path_list = [paths]
    else:
        path_list = list(paths)

    found: List[str] = []
    seen = set()
    for p in path_list:
        expanded = os.path.expanduser(p)
        if os.path.isfile(expanded):
            ext = os.path.splitext(expanded)[1].lower()
            abs_p = os.path.abspath(expanded)
            if ext in SUPPORTED_EXTENSIONS and abs_p not in seen:
                seen.add(abs_p)
                found.append(abs_p)
        elif os.path.isdir(expanded):
            for root, _dirs, files in os.walk(expanded):
                for fn in sorted(files):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        abs_p = os.path.abspath(os.path.join(root, fn))
                        if abs_p not in seen:
                            seen.add(abs_p)
                            found.append(abs_p)
    return found


def index_local_books(
    paths: Union[str, List[str]],
    db_path: str = DEFAULT_DB_PATH,
    force: bool = False,
    max_pages_per_pdf: int = 1000
) -> Dict[str, Any]:
    """
    Index all supported book/document files (.pdf, .epub, .txt, .md) in `paths`
    into a SQLite FTS5 (`unicode61` + `trigram`) database.
    """
    t0 = time.time()
    candidate_files = _discover_files(paths)
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    _init_db(conn)

    indexed_files = 0
    skipped_files = 0
    total_pages_indexed = 0
    errors: List[Dict[str, str]] = []

    try:
        for fpath in candidate_files:
            try:
                stat = os.stat(fpath)
            except OSError as e:
                errors.append({"file": fpath, "error": str(e)})
                continue

            cur = conn.execute(
                "SELECT book_id, mtime, file_size, page_count FROM books WHERE file_path = ?",
                (fpath,)
            )
            row = cur.fetchone()
            if (
                row
                and not force
                and abs(float(row[1]) - stat.st_mtime) < 1e-3
                and int(row[2]) == stat.st_size
            ):
                skipped_files += 1
                total_pages_indexed += int(row[3])
                continue

            try:
                pages = extract_document_pages(fpath, max_pages=max_pages_per_pdf)
            except Exception as e:
                errors.append({"file": fpath, "error": str(e)})
                continue

            fname = os.path.basename(fpath)
            clean_title = strip_nikkud(fix_legacy_hebrew_pdf(os.path.splitext(fname)[0]))
            ext = os.path.splitext(fname)[1].lower().lstrip(".")

            with conn:
                if row:
                    old_book_id = int(row[0])
                    old_page_ids = [
                        r[0] for r in conn.execute("SELECT id FROM book_pages WHERE book_id = ?", (old_book_id,))
                    ]
                    for pid in old_page_ids:
                        conn.execute("DELETE FROM book_pages_fts WHERE rowid = ?", (pid,))
                        conn.execute("DELETE FROM book_pages_trigram WHERE rowid = ?", (pid,))
                    conn.execute("DELETE FROM book_pages WHERE book_id = ?", (old_book_id,))
                    conn.execute("DELETE FROM books WHERE book_id = ?", (old_book_id,))

                cur_ins = conn.execute(
                    """
                    INSERT INTO books (file_path, file_name, file_type, file_size, mtime, page_count, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (fpath, fname, ext, stat.st_size, stat.st_mtime, len(pages), time.time())
                )
                book_id = int(cur_ins.lastrowid)

                for page_num, raw_txt, clean_txt in pages:
                    p_cur = conn.execute(
                        """
                        INSERT INTO book_pages (book_id, title, file_path, page_num, raw_text, clean_text)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (book_id, clean_title, fpath, page_num, raw_txt, clean_txt)
                    )
                    rowid = int(p_cur.lastrowid)
                    conn.execute(
                        "INSERT INTO book_pages_fts(rowid, title, clean_text) VALUES (?, ?, ?)",
                        (rowid, clean_title, clean_txt)
                    )
                    conn.execute(
                        "INSERT INTO book_pages_trigram(rowid, title, clean_text) VALUES (?, ?, ?)",
                        (rowid, clean_title, clean_txt)
                    )

            indexed_files += 1
            total_pages_indexed += len(pages)

        db_total_row = conn.execute("SELECT COUNT(*) FROM book_pages").fetchone()
        if db_total_row:
            total_pages_indexed = max(total_pages_indexed, int(db_total_row[0]))
    finally:
        conn.close()

    elapsed_s = round(time.time() - t0, 3)
    return {
        "status": "success",
        "db_path": os.path.abspath(db_path),
        "files_discovered": len(candidate_files),
        "files_indexed": indexed_files,
        "files_skipped_unchanged": skipped_files,
        "total_pages_in_index": total_pages_indexed,
        "elapsed_seconds": elapsed_s,
        "errors": errors,
    }


def search_local_books(
    query: str,
    db_path: str = DEFAULT_DB_PATH,
    limit: int = 10,
    expand_hebrew: bool = True
) -> List[Dict[str, Any]]:
    """
    Search the local books FTS5 database using Hebrew morphological prefix/sofit expansion
    with automatic fallback to trigram / substring search.
    """
    limit = max(0, limit)
    if limit == 0 or not query or not query.strip():
        return []

    if not os.path.exists(db_path):
        return [{
            "error": f"Local books index database not found at {db_path}. Run index_local_books first.",
            "source": "local_books"
        }]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    results: List[Dict[str, Any]] = []

    try:
        fts_query = build_fts5_query(query, expand_hebrew=expand_hebrew)
        rows = []
        if fts_query:
            sql = """
                SELECT
                    bp.id,
                    bp.title,
                    bp.file_path,
                    bp.page_num,
                    bp.raw_text,
                    bp.clean_text,
                    snippet(book_pages_fts, 1, '«', '»', ' ... ', 24) AS snippet,
                    bm25(book_pages_fts) AS rank_score
                FROM book_pages_fts
                JOIN book_pages bp ON bp.id = book_pages_fts.rowid
                WHERE book_pages_fts MATCH ?
                ORDER BY rank_score
                LIMIT ?
            """
            try:
                rows = conn.execute(sql, (fts_query, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []

        # Fallback to trigram or LIKE substring search if FTS5 prefix query returned 0 hits
        clean_sub = strip_nikkud(query).strip()
        if not rows and len(clean_sub) >= 3:
            sql_tri = """
                SELECT
                    bp.id,
                    bp.title,
                    bp.file_path,
                    bp.page_num,
                    bp.raw_text,
                    bp.clean_text,
                    snippet(book_pages_trigram, 1, '«', '»', ' ... ', 24) AS snippet,
                    bm25(book_pages_trigram) AS rank_score
                FROM book_pages_trigram
                JOIN book_pages bp ON bp.id = book_pages_trigram.rowid
                WHERE book_pages_trigram MATCH ?
                ORDER BY rank_score
                LIMIT ?
            """
            escaped_sub = clean_sub.replace('"', '""')
            try:
                rows = conn.execute(sql_tri, (f'"{escaped_sub}"', limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []

        if not rows and clean_sub:
            sql_like = """
                SELECT
                    id, title, file_path, page_num, raw_text, clean_text,
                    substr(clean_text, max(1, instr(clean_text, ?) - 60), 220) AS snippet,
                    0.0 AS rank_score
                FROM book_pages
                WHERE clean_text LIKE ? OR title LIKE ?
                LIMIT ?
            """
            like_pat = f"%{clean_sub}%"
            rows = conn.execute(sql_like, (clean_sub, like_pat, like_pat, limit)).fetchall()

        for r in rows:
            results.append({
                "title": r["title"],
                "file_path": r["file_path"],
                "file_name": os.path.basename(r["file_path"]),
                "page_num": r["page_num"],
                "snippet": r["snippet"],
                "score": round(abs(float(r["rank_score"] or 0.0)), 4),
                "source": "local_books",
            })
    finally:
        conn.close()

    return results


def get_local_book_page(
    file_path_or_title: str,
    page_num: int = 1,
    db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    """Retrieve the full extracted text of a specific page from an indexed local book."""
    if not os.path.exists(db_path):
        return {"error": f"Index database not found at {db_path}", "source": "local_books"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        target = file_path_or_title.strip()
        row = conn.execute(
            """
            SELECT title, file_path, page_num, raw_text, clean_text
            FROM book_pages
            WHERE (file_path = ? OR title LIKE ? OR file_path LIKE ?) AND page_num = ?
            LIMIT 1
            """,
            (target, f"%{strip_nikkud(target)}%", f"%{target}%", max(1, page_num))
        ).fetchone()
        if not row:
            return {
                "error": f"Page {page_num} not found for '{file_path_or_title}'",
                "source": "local_books"
            }
        return {
            "status": "success",
            "title": row["title"],
            "file_path": row["file_path"],
            "page_num": row["page_num"],
            "raw_text": row["raw_text"],
            "clean_text": row["clean_text"],
            "source": "local_books"
        }
    finally:
        conn.close()


# ==============================================================================
# 4. Model Context Protocol (MCP) & CLI Runner
# ==============================================================================
def run_mcp_server():
    """Hardened stdio Model Context Protocol (MCP) server for local book library search."""
    tools_def = [
        {
            "name": "index_local_books",
            "description": "Index local PDF, EPUB, TXT, and Markdown books into the SQLite FTS5 search engine.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "string",
                        "description": "Directory path or file path to index (e.g. '/mnt/c/Users/avrahamas/Downloads')"
                    },
                    "force": {"type": "boolean", "default": False}
                },
                "required": ["paths"]
            }
        },
        {
            "name": "search_local_books",
            "description": "Full-text search across indexed local books with Hebrew nikkud stripping, prefix expansion, and legacy PDF repair.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Hebrew or English search keywords"},
                    "limit": {"type": "integer", "default": 10},
                    "expand_hebrew": {"type": "boolean", "default": True}
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_local_book_page",
            "description": "Fetch full text of a specific page from an indexed local book.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path_or_title": {"type": "string", "description": "Book file path or title substring"},
                    "page_num": {"type": "integer", "default": 1}
                },
                "required": ["file_path_or_title"]
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
                        "serverInfo": {"name": "local-books-server", "version": "1.0.0"}
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
                if name in ("index_local_books", "local_books_index"):
                    raw_paths = args.get("paths", "")
                    paths_arg = raw_paths if isinstance(raw_paths, list) else str(raw_paths)
                    out = index_local_books(
                        paths=paths_arg,
                        force=bool(args.get("force", False))
                    )
                    if isinstance(out, dict) and "error" in out:
                        is_err = True
                elif name in ("search_local_books", "local_books_search"):
                    out = search_local_books(
                        query=str(args.get("query", "")),
                        limit=safe_int(args.get("limit"), 10),
                        expand_hebrew=bool(args.get("expand_hebrew", True))
                    )
                    if isinstance(out, list) and len(out) > 0 and isinstance(out[0], dict) and "error" in out[0]:
                        is_err = True
                elif name in ("get_local_book_page", "local_books_get_page"):
                    out = get_local_book_page(
                        file_path_or_title=str(args.get("file_path_or_title", "")),
                        page_num=safe_int(args.get("page_num"), 1)
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
        parser = argparse.ArgumentParser(description="Local Book Library Indexer & Search Engine")
        parser.add_argument("--index", "-i", type=str, help="Directory or file path to index")
        parser.add_argument("--query", "-q", type=str, help="Search query")
        parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="SQLite database path")
        parser.add_argument("--limit", "-l", type=int, default=5, help="Max search results")
        parser.add_argument("--force", action="store_true", help="Force re-indexing unchanged files")
        args = parser.parse_args()

        if args.index:
            stats = index_local_books(args.index, db_path=args.db, force=args.force)
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        if args.query:
            hits = search_local_books(args.query, db_path=args.db, limit=args.limit)
            print(json.dumps(hits, ensure_ascii=False, indent=2))
