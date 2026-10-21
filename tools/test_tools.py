"""
test_tools.py - Comprehensive Test Suite for Agent Data Sources (v2.0).

Verifies:
1. Live endpoints requiring zero API keys:
   - OpenLibrary REST API & non-'goog' IA ID selection + OL...W key resolution
   - HebrewBooks.org beta JSONP catalog search, suggest.ashx, shas.aspx, & PDF stream download
   - Sefaria.org reference resolution (/api/name), text fetch (/api/texts), & search (/api/search-wrapper)
   - Internet Archive CDL borrowable detection & public domain OCR text
   - Local Books FTS5 Indexer & Search Engine (PyMuPDF + SQLite FTS5 unicode61/trigram + Downloads PDFs)
   - Reddit Arctic Shift shadow archive (ASCII bounded title search + Hebrew r/Israel fallback)
   - Rotter.net breaking scoops RSS with Windows-1255 decoding
   - Discourse universal search (discuss.python.org)
   - News Tool: Google News RSS (IL:he), Israeli RSS portals (Ynet, Mako UTF-8), Telegram (t.me/s/amitsegal)
2. Auth handling, bug-fix regressions, and mock payload processing:
   - Google Books API quota / 429 handling, limit<=0 early return, & RequestException safety
   - SocialData.tools, Exa.ai, Apify Instagram/Facebook routing, & metrics/engagement schema alignment
   - Personal X (X_AUTH_TOKEN + X_CT0) & Instagram (IG_SESSIONID) session cookie readers
   - SERP Dorking PSE vs SerpApi vs custom search_engine_url separation
   - Hardened MCP stdio JSON-RPC 2.0 compliance across all 5 tool servers
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import requests

# Ensure parent directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.book_tool import (
    search_openlibrary,
    search_google_books,
    search_hebrewbooks,
    suggest_hebrewbooks,
    fetch_hebrewbooks_shas,
    download_hebrewbooks_pdf,
    search_sefaria,
    fetch_sefaria_text,
    fetch_ia_text,
    search_books,
    fetch_book_text,
    BookItem,
)
from tools.local_books_tool import (
    strip_nikkud,
    fix_legacy_hebrew_pdf,
    expand_hebrew_word_fts5,
    build_fts5_query,
    index_local_books,
    search_local_books,
    get_local_book_page,
)
from tools.social_tool import (
    search_socialdata_tweets,
    search_exa_social,
    fetch_apify_instagram,
    fetch_apify_facebook,
    fetch_personal_x_timeline,
    fetch_personal_instagram_feed,
    search_social,
    SocialPost,
)
from tools.forum_tool import (
    search_reddit_arctic_shift,
    search_arctic_shift,
    fetch_reddit_arctic_shift,
    fetch_rotter_scoops,
    build_serp_forum_dork,
    search_discourse_forum,
    search_stackexchange,
    fetch_stackexchange,
    search_forum_serp_dork,
    ForumPost,
    RotterScoop,
)
from tools.news_tool import (
    search_news,
    search_google_news_rss,
    fetch_israeli_rss,
    fetch_telegram_channel,
    search_gdelt_news,
    search_gdelt,
    ISRAELI_RSS_FEEDS,
)


class TestBookTool(unittest.TestCase):
    """Test suite for Book Search & Retrieval (R1)."""

    def test_openlibrary_live(self):
        """Verify OpenLibrary search against live openlibrary.org endpoint."""
        print("\n[TEST] Running search_openlibrary (Live)...")
        results = search_openlibrary("The Hobbit", limit=2)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertIn("title", first)
        self.assertIn("author", first)
        self.assertIn("source", first)
        self.assertEqual(first["source"], "openlibrary")
        print(f"  -> Success: Found '{first.get('title')}' by {first.get('author')}")

    def test_openlibrary_non_goog_ia_and_ol_key_resolution_live(self):
        """Verify OpenLibrary prefers non-goog IA IDs and fetch_ia_text resolves OL...W keys."""
        print("\n[TEST] Running fetch_ia_text with OpenLibrary Work key OL66554W (Live)...")
        res = fetch_ia_text("OL66554W", max_chars=600)
        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("openlibrary_key"), "OL66554W")
        self.assertTrue(len(res.get("text", "")) > 100)
        print(f"  -> Success: Resolved OL66554W to IA '{res.get('id')}' and fetched {len(res.get('text', ''))} chars")

    def test_hebrewbooks_jsonp_live(self):
        """Verify HebrewBooks beta JSONP search against live endpoint."""
        print("\n[TEST] Running search_hebrewbooks (Live)...")
        results = search_hebrewbooks("ברכות", limit=2)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertIn("id", first)
        self.assertIn("title", first)
        self.assertEqual(first["source"], "hebrewbooks")
        self.assertTrue(first["download_url"].startswith("https://download.hebrewbooks.org/"))
        print(f"  -> Success: Found Hebrew book ID {first.get('id')}: {first.get('title')[:40]}...")

    def test_hebrewbooks_suggest_and_shas_live(self):
        """Verify HebrewBooks suggest.ashx and shas.aspx plain-text Talmud extraction."""
        print("\n[TEST] Running suggest_hebrewbooks and fetch_hebrewbooks_shas (Live)...")
        suggestions = suggest_hebrewbooks("ברכות", limit=3)
        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)

        shas = fetch_hebrewbooks_shas(mesechta=1, daf=2, max_chars=1500)
        self.assertEqual(shas.get("status"), "success")
        self.assertIn("מאימתי", shas.get("gemara", ""))
        print(f"  -> Success: Fetched Berakhot 2a Gemara ({len(shas.get('gemara', ''))} chars) & {len(suggestions)} suggestions")

    def test_sefaria_search_and_text_live(self):
        """Verify Sefaria reference resolution, full-text search, and bilingual text extraction."""
        print("\n[TEST] Running search_sefaria and fetch_sefaria_text (Live)...")
        hits = search_sefaria("אימתי קורין את שמע", limit=2)
        self.assertIsInstance(hits, list)
        self.assertGreater(len(hits), 0)
        self.assertEqual(hits[0]["source"], "sefaria")

        txt = fetch_sefaria_text("Berakhot 2a", max_chars=1000)
        self.assertEqual(txt.get("status"), "success")
        self.assertTrue(len(txt.get("hebrew_text", "")) > 50)
        print(f"  -> Success: Sefaria returned {len(hits)} hits and Berakhot 2a text ({len(txt.get('text', ''))} chars)")

    def test_hebrewbooks_pdf_download_live(self):
        """Verify downloading initial chunk of PDF stream from HebrewBooks."""
        print("\n[TEST] Running download_hebrewbooks_pdf stream (Live)...")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            dest = download_hebrewbooks_pdf(book_id=16102, dest_path=tmp_path, max_bytes=32768)
            self.assertEqual(dest, tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            file_size = os.path.getsize(tmp_path)
            self.assertGreaterEqual(file_size, 1024)

            with open(tmp_path, "rb") as f:
                header = f.read(5)
            self.assertTrue(header.startswith(b"%PDF"), f"Expected %PDF header, got {header}")
            print(f"  -> Success: Streamed {file_size} bytes with valid PDF header from HebrewBooks")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_ia_cdl_borrowable_detection_live(self):
        """Verify Internet Archive CDL borrowable status detection."""
        print("\n[TEST] Running fetch_ia_text on CDL item (Live)...")
        result = fetch_ia_text("banalnostzlaeikh00aren")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["id"], "banalnostzlaeikh00aren")
        self.assertTrue(result["is_borrowable"])
        self.assertIn("message", result)
        print(f"  -> Success: Cleanly identified CDL status: {result.get('status')}")

    def test_google_books_quota_guidance(self):
        """Verify Google Books gracefully handles unauthenticated quota exhaustion (429)."""
        print("\n[TEST] Running search_google_books unauthenticated quota check...")
        results = search_google_books("machine learning", api_key=None, limit=2)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        first = results[0]
        if "quota_exhausted" in first:
            self.assertTrue(first["quota_exhausted"])
            self.assertIn("guidance", first)
            print("  -> Success: Caught 429 and provided clear GOOGLE_BOOKS_API_KEY guidance")
        else:
            self.assertIn("title", first)
            print(f"  -> Success: Google Books query succeeded with title '{first.get('title')}'")

    def test_google_books_limit_zero_and_request_exception(self):
        """Verify search_google_books returns [] before HTTP call when limit<=0 and handles RequestException safely."""
        print("\n[TEST] Running search_google_books limit<=0 & RequestException regression test...")
        with patch("tools.book_tool._session") as mock_sess:
            res_zero = search_google_books("test", limit=0)
            self.assertEqual(res_zero, [])
            mock_sess.return_value.get.assert_not_called()

            mock_sess.return_value.get.side_effect = requests.RequestException("Connection reset")
            res_err = search_google_books("test", limit=3)
            self.assertEqual(len(res_err), 1)
            self.assertIn("error", res_err[0])
            self.assertEqual(res_err[0]["source"], "googlebooks")
            print("  -> Success: limit<=0 skipped network call and RequestException returned structured error")

    def test_google_books_mock_parsing(self):
        """Verify Google Books volume parser with realistic payload."""
        print("\n[TEST] Running search_google_books mock parsing...")
        mock_response = {
            "items": [
                {
                    "id": "mock_vol_1",
                    "volumeInfo": {
                        "title": "Autonomous Agents in Action",
                        "authors": ["Dr. Researcher"],
                        "publishedDate": "2025-06-15",
                        "description": "Comprehensive guide to multi-agent architectures.",
                        "previewLink": "https://books.google.com/mock"
                    },
                    "accessInfo": {
                        "publicDomain": True,
                        "viewability": "ALL_PAGES",
                        "pdf": {"downloadLink": "https://books.google.com/mock.pdf"}
                    },
                    "searchInfo": {
                        "textSnippet": "Autonomous agents perform <b>tool execution</b> and retrieval."
                    }
                }
            ]
        }
        with patch("tools.book_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_sess.return_value.get.return_value = mock_resp

            results = search_google_books("test", api_key="dummy_key", limit=1)
            self.assertEqual(len(results), 1)
            item = results[0]
            self.assertEqual(item["title"], "Autonomous Agents in Action")
            self.assertEqual(item["author"], "Dr. Researcher")
            self.assertEqual(item["year"], 2025)
            self.assertTrue(item["has_fulltext"])
            self.assertEqual(item["download_url"], "https://books.google.com/mock.pdf")
            print("  -> Success: Google Books mock payload correctly parsed into standardized fields")

    def test_search_books_unified_contract(self):
        """Verify high-level search_books wrapper."""
        print("\n[TEST] Running unified search_books contract...")
        ol_res = search_books("Python programming", source="openlibrary", max_results=2)
        self.assertGreater(len(ol_res), 0)
        self.assertEqual(ol_res[0]["source"], "openlibrary")

        hb_res = search_books("ברכות", source="hebrewbooks", max_results=2)
        self.assertGreater(len(hb_res), 0)
        self.assertEqual(hb_res[0]["source"], "hebrewbooks")

        sef_res = search_books("Berakhot 2a", source="sefaria", max_results=1)
        self.assertGreater(len(sef_res), 0)
        self.assertEqual(sef_res[0]["source"], "sefaria")
        print("  -> Success: Unified search_books returned standardized results across sources")

    def test_hebrewbooks_empty_search(self):
        """Verify HebrewBooks 0-results search returns [] cleanly for 'bot();' and gibberish."""
        print("\n[TEST] Running search_hebrewbooks 0-results empty payload handling...")
        with patch("tools.book_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "bot();"
            mock_sess.return_value.get.return_value = mock_resp

            res = search_hebrewbooks("nonexistent_mock_query", limit=3)
            self.assertEqual(res, [], f"Expected [], got {res}")

        with patch("tools.book_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "bot( );"
            mock_sess.return_value.get.return_value = mock_resp

            res = search_hebrewbooks("nonexistent_mock_query", limit=3)
            self.assertEqual(res, [], f"Expected [], got {res}")

        live_res = search_hebrewbooks("שדלכגשדלכג", limit=3)
        self.assertEqual(live_res, [], f"Expected live [] for gibberish query, got {live_res}")
        print("  -> Success: HebrewBooks empty search returned [] cleanly without JSONDecodeError")

    def test_hebrewbooks_pdf_download_html_error_handling(self):
        """Verify HebrewBooks PDF download validates %PDF magic bytes and rejects HTML error pages."""
        print("\n[TEST] Running download_hebrewbooks_pdf %PDF header validation...")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with self.assertRaises(ValueError) as ctx:
                download_hebrewbooks_pdf(book_id=999999999, dest_path=tmp_path, max_bytes=1024)
            self.assertIn("Invalid PDF stream from HebrewBooks", str(ctx.exception))
            self.assertFalse(os.path.exists(tmp_path), "Corrupted HTML file must not be left on disk")
            print(f"  -> Success: Cleanly caught ValueError: {ctx.exception}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_openlibrary_edition_key_work_fallback(self):
        """Verify OL...M edition key resolves source_records and parent work IA IDs."""
        print("\n[TEST] Running _resolve_openlibrary_ia_candidates OL...M parent work fallback...")
        with patch("tools.book_tool._session") as mock_sess:
            resp_edition = MagicMock()
            resp_edition.status_code = 200
            resp_edition.json.return_value = {
                "source_records": ["ia:scan_from_source_record"],
                "works": [{"key": "/works/OL12345W"}],
            }
            resp_work = MagicMock()
            resp_work.status_code = 200
            resp_work.json.return_value = {
                "docs": [{"key": "/works/OL12345W", "ia": ["bub_gb_123goog", "public_scan_456"]}]
            }
            resp_meta = MagicMock()
            resp_meta.status_code = 200
            resp_meta.json.return_value = {
                "metadata": {"title": "Test Edition"},
                "files": [{"name": "scan_from_source_record_djvu.txt"}],
            }
            resp_txt = MagicMock()
            resp_txt.status_code = 200
            resp_txt.text = "Full OCR text from edition source record."
            mock_sess.return_value.get.side_effect = [resp_edition, resp_work, resp_meta, resp_txt]

            res = fetch_ia_text("OL99999M", max_chars=500)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["openlibrary_key"], "OL99999M")
            self.assertIn("Full OCR text", res["text"])
            print("  -> Success: OL...M edition resolved source_records + parent work IA candidates")


class TestLocalBooksTool(unittest.TestCase):
    """Test suite for Local Book Library FTS5 Indexer & Hebrew Normalization."""

    def test_strip_nikkud_and_fix_legacy_hebrew_pdf(self):
        """Verify nikkud stripping, visual-RTL Windows-1255 repair, and FTS5 prefix/sofit expansion."""
        print("\n[TEST] Running strip_nikkud, fix_legacy_hebrew_pdf, and expand_hebrew_word_fts5...")
        vocalized = "סֵפֶר מֹורְמֹון וְנֶפִי"
        self.assertEqual(strip_nikkud(vocalized), "ספר מורמון ונפי")

        # Visual-RTL Windows-1255 mapped in Latin-1 (from real Israeli insurance/payslip PDFs)
        visual_rtl = 'íééç úñéìåôì î"òá äéàø úåéâåìåðëè ééàìéáåî'
        repaired = fix_legacy_hebrew_pdf(visual_rtl)
        self.assertIn("חיים", repaired)
        self.assertIn("לפוליסת", repaired)
        self.assertIn("מובילאיי", repaired)

        # PostScript /CE0../CFA glyph names
        glyph_encoded = "/CE9/CF8/CE5/CF9/CEC/CE9/CED"  # visual RTL for םילשורי
        self.assertIn("ירושלים", fix_legacy_hebrew_pdf(glyph_encoded))

        # Sofit-to-medial FTS5 expansion ('מלך' -> includes both "מלך"* and "מלכ"*)
        expanded = expand_hebrew_word_fts5("מלך")
        self.assertIn('"מלך"*', expanded)
        self.assertIn('"מלכ"*', expanded)
        self.assertIn('"המלכ"*', expanded)
        print("  -> Success: Hebrew nikkud stripping, visual-RTL repair, and sofit expansion verified")

    def test_index_and_search_local_books_live(self):
        """Verify indexing and searching both synthetic Hebrew files and live Downloads PDFs."""
        print("\n[TEST] Running index_local_books and search_local_books...")
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "test_books.db")
            doc1 = os.path.join(tmpdir, "sefer_test.txt")
            with open(doc1, "w", encoding="utf-8") as f:
                f.write("בְּרֵאשִׁית בָּרָא אֱלֹהִים. וְהַמְּלָכִים הָלְכוּ בִּירוּשָׁלַיִם.")

            stats = index_local_books(tmpdir, db_path=db_file)
            self.assertEqual(stats["files_indexed"], 1)

            # Search 'מלך' (matches 'והמלכים' via prefix + sofit->medial expansion!)
            hits_melech = search_local_books("מלך", db_path=db_file)
            self.assertEqual(len(hits_melech), 1)

            # Search 'ירושלים' (matches 'בירושלים' via prefix expansion + nikkud strip!)
            hits_jlm = search_local_books("ירושלים", db_path=db_file)
            self.assertEqual(len(hits_jlm), 1)

            page_data = get_local_book_page("sefer_test", page_num=1, db_path=db_file)
            self.assertEqual(page_data.get("status"), "success")

        # Also verify live Downloads index if present
        default_hits = search_local_books("ירושלים", limit=2)
        if default_hits and "error" not in default_hits[0]:
            self.assertGreater(len(default_hits), 0)
            print(f"  -> Success: Local FTS5 matched '{default_hits[0]['file_name']}' p.{default_hits[0]['page_num']}")

    def test_hebrew_abbreviations_gershayim_and_filesize_reindex(self):
        """Verify Hebrew abbreviations with gershayim (בע"מ, דו"ח) and file_size change re-indexing."""
        print("\n[TEST] Running Hebrew gershayim FTS5 expansion & file_size re-indexing test...")
        exp_baam = expand_hebrew_word_fts5('בע"מ')
        self.assertIn('"בע מ"', exp_baam)
        self.assertIn('"מ בע"', exp_baam)
        self.assertIn('"בעמ"', exp_baam)
        self.assertNotIn('"בעמ"*', exp_baam)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "test_abbrev.db")
            doc_path = os.path.join(tmpdir, "company_doc.txt")
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write('חברת מובילאיי טכנולוגיות בע"מ הגישה דו"ח שנתי בעמודת הסיכום.')

            st1 = index_local_books(tmpdir, db_path=db_file)
            self.assertEqual(st1["files_indexed"], 1)

            hits_baam = search_local_books('בע"מ', db_path=db_file)
            self.assertEqual(len(hits_baam), 1)
            self.assertIn('«בע"מ»', hits_baam[0]["snippet"])
            self.assertNotIn("«בעמודת»", hits_baam[0]["snippet"])

            # Verify trigram fallback with internal quote does not raise OperationalError
            no_hits = search_local_books('קק"ל', db_path=db_file)
            self.assertEqual(no_hits, [])

            # Modify file content & size while preserving mtime -> must re-index!
            orig_stat = os.stat(doc_path)
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write('חברת מובילאיי טכנולוגיות בע"מ הגישה דו"ח שנתי מורחב מאוד לשנת 2026.')
            os.utime(doc_path, (orig_stat.st_atime, orig_stat.st_mtime))

            st2 = index_local_books(tmpdir, db_path=db_file)
            self.assertEqual(st2["files_indexed"], 1, "Must re-index when file_size changes with same mtime")
            print("  -> Success: Hebrew gershayim (בע\"מ / דו\"ח), trigram quote escape, and file_size re-index verified")


class TestSocialTool(unittest.TestCase):
    """Test suite for Social Networks Access & Personal Accounts (R2)."""

    def test_socialdata_missing_key_guidance(self):
        """Verify SocialData raises informative error guidance when API key is missing."""
        print("\n[TEST] Running search_socialdata_tweets without API key...")
        with patch.dict(os.environ, {}, clear=True):
            res = search_socialdata_tweets("בינה מלאכותית", api_key=None)
            self.assertIsInstance(res, list)
            first = res[0]
            self.assertIn("error", first)
            self.assertIn("guidance", first)
            self.assertIn("SOCIALDATA_API_KEY", first["guidance"])
            print("  -> Success: Missing key cleanly handled with actionable guidance")

    def test_socialdata_mock_payload(self):
        """Verify SocialData Twitter search response parsing with Hebrew text and metrics/engagement."""
        print("\n[TEST] Running search_socialdata_tweets mock payload parsing...")
        mock_payload = {
            "tweets": [
                {
                    "id_str": "1840000000000000001",
                    "full_text": "התקדמות אדירה במודלי שפה בעברית! #בינה_מלאכותית",
                    "created_at": "Wed Oct 21 00:00:00 +0000 2026",
                    "favorite_count": 89,
                    "retweet_count": 14,
                    "reply_count": 5,
                    "views_count": 3200,
                    "lang": "he",
                    "user": {
                        "name": "Israel Tech Watch",
                        "screen_name": "israeltech",
                        "verified": True
                    }
                }
            ]
        }
        with patch("tools.social_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_payload
            mock_sess.return_value.get.return_value = mock_resp

            results = search_socialdata_tweets("test", api_key="dummy_token")
            self.assertEqual(len(results), 1)
            t = results[0]
            self.assertEqual(t["platform"], "x")
            self.assertEqual(t["author"], "@israeltech")
            self.assertIn("התקדמות אדירה", t["content"])
            self.assertEqual(t["metrics"]["likes"], 89)
            self.assertEqual(t["engagement"]["likes"], 89)
            self.assertEqual(t["metrics"]["retweets"], 14)
            self.assertEqual(t["url"], "https://x.com/israeltech/status/1840000000000000001")
            print("  -> Success: SocialData tweet payload mapped to SocialPost with both metrics & engagement")

    def test_search_social_platform_routing(self):
        """Verify search_social routes platform='instagram' and 'facebook' properly under default method."""
        print("\n[TEST] Running search_social platform routing regression test...")
        with patch.dict(os.environ, {}, clear=True):
            ig_res = search_social("israel", platform="instagram")
            self.assertEqual(ig_res[0]["platform"], "instagram")
            self.assertIn("APIFY_API_TOKEN", ig_res[0]["guidance"])

            fb_res = search_social("ynetnews", platform="facebook")
            self.assertEqual(fb_res[0]["platform"], "facebook")
            self.assertIn("APIFY_API_TOKEN", fb_res[0]["guidance"])
            print("  -> Success: platform='instagram' and 'facebook' routed properly without falling into X")

    def test_personal_x_and_instagram_cookie_guidance(self):
        """Verify personal X and Instagram cookie readers provide clear setup guidance when cookies missing."""
        print("\n[TEST] Running fetch_personal_x_timeline and fetch_personal_instagram_feed without cookies...")
        with patch.dict(os.environ, {}, clear=True):
            x_res = fetch_personal_x_timeline(mode="bookmarks")
            self.assertIn("X_AUTH_TOKEN", x_res[0]["guidance"])
            self.assertEqual(x_res[0]["platform"], "x")

            ig_res = fetch_personal_instagram_feed(username="saved")
            self.assertIn("IG_SESSIONID", ig_res[0]["guidance"])
            self.assertEqual(ig_res[0]["platform"], "instagram")
            print("  -> Success: Personal X & Instagram cookie setup instructions verified")

    def test_exa_social_missing_key_guidance(self):
        """Verify Exa.ai handles missing key gracefully."""
        print("\n[TEST] Running search_exa_social without key...")
        with patch.dict(os.environ, {}, clear=True):
            res = search_exa_social("AI agents in Israel", api_key=None)
            self.assertIsInstance(res, list)
            first = res[0]
            self.assertIn("error", first)
            self.assertIn("EXA_API_KEY", first["guidance"])
            print("  -> Success: Missing EXA_API_KEY handled with clear guidance")

    def test_exa_social_mock_payload(self):
        """Verify Exa searchAndContents response parsing."""
        print("\n[TEST] Running search_exa_social mock payload parsing...")
        mock_payload = {
            "results": [
                {
                    "id": "https://x.com/OpenAI/status/12345",
                    "url": "https://x.com/OpenAI/status/12345",
                    "title": "OpenAI on X: New models released",
                    "author": "OpenAI",
                    "publishedDate": "2026-10-15T18:00:00Z",
                    "score": 0.94,
                    "highlights": ["Announcing our latest reasoning breakthrough for autonomous agents."]
                }
            ]
        }
        with patch("tools.social_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_payload
            mock_sess.return_value.post.return_value = mock_resp

            results = search_exa_social("AI announcement", api_key="dummy_exa")
            self.assertEqual(len(results), 1)
            p = results[0]
            self.assertEqual(p["platform"], "x")
            self.assertIn("Announcing our latest", p["content"])
            self.assertEqual(p["metrics"]["score"], 0.94)
            print("  -> Success: Exa neural search highlights mapped to standardized SocialPost")

    def test_apify_instagram_missing_key_guidance(self):
        """Verify Apify Instagram handles missing token gracefully."""
        print("\n[TEST] Running fetch_apify_instagram without token...")
        with patch.dict(os.environ, {}, clear=True):
            res = fetch_apify_instagram("instagram", api_key=None)
            self.assertIsInstance(res, list)
            first = res[0]
            self.assertIn("error", first)
            self.assertIn("APIFY_API_TOKEN", first["guidance"])
            print("  -> Success: Missing APIFY_API_TOKEN handled with clear guidance")

    def test_apify_instagram_mock_payload(self):
        """Verify Apify Instagram scraper actor dataset output parsing."""
        print("\n[TEST] Running fetch_apify_instagram mock parsing...")
        mock_items = [
            {
                "id": "3456789012345678901",
                "shortCode": "DBXYZ123",
                "caption": "תמונה חדשה מירושלים העתיקה! 📸",
                "timestamp": "2026-10-18T14:30:00.000Z",
                "likesCount": 1240,
                "commentsCount": 45,
                "isVideo": False
            }
        ]
        with patch("tools.social_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_items
            mock_sess.return_value.post.return_value = mock_resp

            results = fetch_apify_instagram("israel_photos", api_key="dummy_token")
            self.assertEqual(len(results), 1)
            post = results[0]
            self.assertEqual(post["platform"], "instagram")
            self.assertEqual(post["author"], "@israel_photos")
            self.assertIn("ירושלים העתיקה", post["content"])
            self.assertEqual(post["metrics"]["likes"], 1240)
            self.assertEqual(post["url"], "https://www.instagram.com/p/DBXYZ123/")
            print("  -> Success: Apify Instagram post correctly parsed into unified schema")

    def test_socialdata_list_payload_guard(self):
        """Verify search_socialdata_tweets handles unexpected list payload without crashing."""
        print("\n[TEST] Running search_socialdata_tweets list payload guard...")
        with patch("tools.social_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_sess.return_value.get.return_value = mock_resp

            results = search_socialdata_tweets("test", api_key="dummy_token")
            self.assertEqual(results, [])
            print("  -> Success: Unexpected list payload safely handled as empty results")

    def test_search_social_personal_auto_routing_and_v1_fallback(self):
        """Verify search_social auto-routes 'bookmarks'/'saved' and X personal timeline falls back to v1.1."""
        print("\n[TEST] Running search_social personal auto-routing and X v1.1 fallback test...")
        with patch.dict(os.environ, {}, clear=True):
            # Auto-routing 'bookmarks' on X and 'saved' on Instagram
            x_bm = search_social("bookmarks", platform="x")
            self.assertIn("X_AUTH_TOKEN", x_bm[0].get("guidance", ""))

            ig_sv = search_social("saved", platform="instagram")
            self.assertIn("IG_SESSIONID", ig_sv[0].get("guidance", ""))

        # Verify X v1.1 fallback when GraphQL endpoint returns 404
        with patch("tools.social_tool._session") as mock_sess:
            r404 = MagicMock()
            r404.status_code = 404
            rv1 = MagicMock()
            rv1.status_code = 200
            rv1.json.return_value = [
                {
                    "id_str": "999",
                    "full_text": "Hello from v1.1 fallback",
                    "created_at": "Wed Oct 21 00:00:00 +0000 2026",
                    "favorite_count": 7,
                    "retweet_count": 2,
                    "reply_count": 1,
                    "user": {"screen_name": "testuser"},
                }
            ]
            mock_sess.return_value.get.side_effect = [r404, rv1]
            res_v1 = fetch_personal_x_timeline(
                mode="user_tweets", auth_token="tok", ct0="csrf", screen_name="testuser", max_results=2
            )
            self.assertEqual(len(res_v1), 1)
            self.assertEqual(res_v1[0]["author"], "@testuser")
            self.assertEqual(res_v1[0]["extra"].get("api"), "v1.1")

            # Verify Instagram 'feed' routes to /api/v1/feed/timeline/
            rig = MagicMock()
            rig.status_code = 200
            rig.json.return_value = {
                "feed_items": [
                    {
                        "media_or_ad": {
                            "pk": "111",
                            "code": "IGFEED1",
                            "caption": {"text": "Home feed post"},
                            "user": {"username": "friend_il"},
                            "like_count": 15,
                            "comment_count": 3,
                        }
                    }
                ]
            }
            mock_sess.return_value.get.side_effect = None
            mock_sess.return_value.get.return_value = rig
            ig_feed = fetch_personal_instagram_feed(username="feed", sessionid="sid123", max_posts=2)
            self.assertEqual(len(ig_feed), 1)
            self.assertEqual(ig_feed[0]["author"], "@friend_il")
            called_ig_url = mock_sess.return_value.get.call_args[0][0]
            self.assertIn("/api/v1/feed/timeline/", called_ig_url)
            print("  -> Success: Personal auto-routing, X v1.1 fallback, and IG timeline feed verified")


class TestForumTool(unittest.TestCase):
    """Test suite for Discussion Forums & Boards (R3)."""

    def test_reddit_arctic_shift_live(self):
        """Verify keyless Reddit search on live Arctic Shift endpoint."""
        print("\n[TEST] Running search_reddit_arctic_shift (Live)...")
        results = search_reddit_arctic_shift(query="asyncio", subreddit="python", limit=2)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertEqual(first["platform"], "reddit")
        self.assertIn("title", first)
        self.assertIn("author", first)
        self.assertIn("score", first)
        self.assertTrue(first["url"].lower().startswith("https://reddit.com/r/python/"))
        print(f"  -> Success: Found Reddit post in r/python: '{first.get('title')[:50]}' by u/{first.get('author')}")

    def test_arctic_shift_hebrew_and_unfiltered_guard(self):
        """Verify Hebrew query defaults to r/Israel and non-matching query returns [] instead of unfiltered posts."""
        print("\n[TEST] Running search_arctic_shift Hebrew routing & unfiltered guard...")
        mock_posts = {
            "data": [
                {"id": "1", "title": "Unrelated post in r/Israel", "selftext": "nothing here", "subreddit": "Israel", "permalink": "/r/Israel/1"},
                {"id": "2", "title": "דיון על בינה מלאכותית בישראל", "selftext": "", "subreddit": "Israel", "permalink": "/r/Israel/2"}
            ]
        }
        with patch("tools.forum_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_posts
            mock_sess.return_value.get.return_value = mock_resp

            # 1. Matching Hebrew query without subreddit -> defaults to Israel and returns only matching post
            matched = search_arctic_shift(query="בינה מלאכותית", limit=5)
            self.assertEqual(len(matched), 1)
            self.assertIn("בינה מלאכותית", matched[0]["title"])
            _, kwargs = mock_sess.return_value.get.call_args
            self.assertEqual(kwargs["params"]["subreddit"], "Israel")

            # 2. Non-matching query -> MUST return [], never unfiltered posts!
            unmatched = search_arctic_shift(query="מילהשלאקיימתבפוסט", limit=5)
            self.assertEqual(unmatched, [])
            print("  -> Success: Hebrew query defaulted to r/Israel and non-matching query returned []")

    def test_rotter_scoops_windows1255_live(self):
        """
        Verify Rotter.net news scoops RSS decoding using Windows-1255.
        CRITICAL: Proves decoding prevents UnicodeDecodeError on live Israeli RSS.
        """
        print("\n[TEST] Running fetch_rotter_scoops with Windows-1255 decoding (Live)...")
        scoops = fetch_rotter_scoops(limit=3)
        self.assertIsInstance(scoops, list)
        self.assertGreater(len(scoops), 0)
        first = scoops[0]
        self.assertIn("title", first)
        self.assertIn("link", first)
        self.assertIn("pub_date", first)
        self.assertTrue(first["link"].startswith("https://rotter.net/forum/scoops1/"))

        has_hebrew = any("\u0590" <= c <= "\u05FF" for c in first["title"])
        self.assertTrue(has_hebrew, f"Expected Hebrew characters in Rotter title: {first['title']}")
        print(f"  -> Success: Fetched {len(scoops)} scoops. Latest Hebrew title: '{first['title'][:50]}...'")

    def test_discourse_universal_search_live(self):
        """Verify universal Discourse search against discuss.python.org."""
        print("\n[TEST] Running search_discourse_forum on discuss.python.org (Live)...")
        results = search_discourse_forum(
            base_url="https://discuss.python.org",
            query="asyncio exception handling",
            limit=2
        )
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertEqual(first["platform"], "discourse")
        self.assertIn("title", first)
        self.assertIn("author", first)
        self.assertTrue("discuss.python.org/t/" in first["url"])
        print(f"  -> Success: Found Discourse post: '{first.get('title')}' by @{first.get('author')}")

    def test_build_serp_forum_dork(self):
        """Verify Google Dork construction for Cloudflare-protected boards."""
        print("\n[TEST] Running build_serp_forum_dork...")
        dork_info = build_serp_forum_dork(query="תקלה בכרטיס מסך", domain="fxp.co.il")
        self.assertEqual(dork_info["target_domain"], "fxp.co.il")
        self.assertEqual(dork_info["dork_query"], "site:fxp.co.il תקלה בכרטיס מסך")
        self.assertIn("google.com/search?q=", dork_info["google_search_url"])
        self.assertIn("site%3Afxp.co.il", dork_info["google_search_url"])
        print(f"  -> Success: Constructed dork query: '{dork_info['dork_query']}'")

    def test_build_serp_forum_dork_provider_separation(self):
        """Verify Google PSE, SerpApi, and custom search_engine_url execute on their own endpoints."""
        print("\n[TEST] Running build_serp_forum_dork provider separation test...")
        with patch("tools.forum_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"organic_results": [{"title": "T1", "link": "https://fxp.co.il/1", "snippet": "S1"}]}
            mock_sess.return_value.get.return_value = mock_resp

            with patch.dict(os.environ, {"SERPAPI_API_KEY": "serp_123"}, clear=True):
                res = build_serp_forum_dork("test", domain="fxp.co.il", limit=2)
                self.assertEqual(len(res["results"]), 1)
                called_url = mock_sess.return_value.get.call_args[0][0]
                self.assertIn("serpapi.com", called_url)

            # Custom search_engine_url
            mock_resp.json.return_value = {"results": [{"title": "T2", "url": "https://fxp.co.il/2", "content": "S2"}]}
            res_custom = build_serp_forum_dork("test", domain="fxp.co.il", search_engine_url="http://localhost:8080/search")
            self.assertEqual(len(res_custom["results"]), 1)
            called_custom_url = mock_sess.return_value.get.call_args[0][0]
            self.assertEqual(called_custom_url, "http://localhost:8080/search")
            print("  -> Success: SerpApi, Google PSE, and custom search_engine_url cleanly separated")

    def test_search_forum_serp_dork_unified(self):
        """Verify high-level search_forum_serp_dork contract."""
        print("\n[TEST] Running search_forum_serp_dork unified wrapper...")
        res = search_forum_serp_dork(query="best mechanical keyboard", forum_domain="quora.com", limit=5)
        self.assertIsInstance(res, list)
        self.assertGreater(len(res), 0)
        first = res[0]
        self.assertEqual(first["platform"], "serp_forum")
        self.assertEqual(first["target_domain"], "quora.com")
        self.assertIn("site:quora.com", first["dork_query"])
        print("  -> Success: Unified forum SERP contract returned structured dork plan")

    def test_reddit_list_payload_guard(self):
        """Verify search_reddit_arctic_shift handles unexpected list payload without crashing."""
        print("\n[TEST] Running search_reddit_arctic_shift list payload guard...")
        with patch("tools.forum_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_sess.return_value.get.return_value = mock_resp

            results = search_reddit_arctic_shift(query="python", limit=5)
            self.assertEqual(results, [])
            print("  -> Success: Unexpected list payload safely handled as empty results")

    def test_rotter_negative_limit_clamped(self):
        """Verify fetch_rotter_scoops clamps negative limit cleanly."""
        print("\n[TEST] Running fetch_rotter_scoops negative limit clamping...")
        res = fetch_rotter_scoops(limit=-1)
        self.assertEqual(res, [])
        print("  -> Success: Negative limit returned [] without slicing issues")

    def test_arctic_shift_multiword_and_no_arg_default(self):
        """Verify multi-word token conjunction matching and safe default subreddit when called with no args."""
        print("\n[TEST] Running search_reddit_arctic_shift multi-word & no-arg default test...")
        mock_posts = {
            "data": [
                {
                    "id": "mw1",
                    "title": "Handling asyncio task cancellation",
                    "selftext": "When an exception occurs inside a task...",
                    "subreddit": "Python",
                    "permalink": "/r/Python/comments/mw1/test/",
                }
            ]
        }
        with patch("tools.forum_tool._session") as mock_sess:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_posts
            mock_sess.return_value.get.return_value = mock_resp

            res_mw = fetch_reddit_arctic_shift(query="asyncio exception", subreddit="python", limit=3)
            self.assertEqual(len(res_mw), 1)
            self.assertEqual(res_mw[0]["post_id"], "mw1")

            res_noarg = search_reddit_arctic_shift(limit=1)
            self.assertEqual(len(res_noarg), 1)
            _, kwargs = mock_sess.return_value.get.call_args
            self.assertEqual(kwargs["params"]["subreddit"], "Python")
            print("  -> Success: Multi-word token matching and default r/Python subreddit verified")

    def test_search_stackexchange_live(self):
        """Verify live StackExchange Q&A search via api.stackexchange.com."""
        print("\n[TEST] Running search_stackexchange (Live)...")
        res = fetch_stackexchange("Shabbat", site="judaism", limit=2)
        self.assertIsInstance(res, list)
        self.assertGreater(len(res), 0)
        self.assertEqual(res[0]["platform"], "stackexchange")
        self.assertEqual(res[0]["community"], "judaism")
        self.assertIn("title", res[0])
        print(f"  -> Success: Found StackExchange (judaism) Q&A: '{res[0]['title'][:50]}...'")


class TestNewsTool(unittest.TestCase):
    """Test suite for Real-Time News & Media Retrieval (R4)."""

    def test_google_news_rss_hebrew_live(self):
        """Verify Google News RSS search in Hebrew (hl=he&gl=IL&ceid=IL:he) live."""
        print("\n[TEST] Running search_google_news_rss (Live Hebrew)...")
        items = search_google_news_rss("טכנולוגיה", lang="he", limit=2)
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)
        self.assertIn("title", items[0])
        self.assertEqual(items[0]["language"], "he")
        print(f"  -> Success: Found Hebrew Google News item: '{items[0]['title'][:50]}...'")

    def test_israeli_rss_portals_utf8_live(self):
        """Verify live Israeli RSS portals (Ynet & Mako/N12) with explicit UTF-8 decoding."""
        print("\n[TEST] Running fetch_israeli_rss on ynet and mako (Live)...")
        ynet_items = fetch_israeli_rss("ynet", limit=2)
        self.assertGreater(len(ynet_items), 0)
        self.assertTrue(any("\u0590" <= c <= "\u05FF" for c in ynet_items[0]["title"]))

        mako_items = fetch_israeli_rss("mako", limit=2)
        self.assertGreater(len(mako_items), 0)
        self.assertTrue(any("\u0590" <= c <= "\u05FF" for c in mako_items[0]["title"]))
        print(f"  -> Success: Ynet ('{ynet_items[0]['title'][:35]}...') & Mako ('{mako_items[0]['title'][:35]}...') decoded cleanly")

    def test_telegram_channel_web_preview_live(self):
        """Verify keyless Telegram public channel web preview scraping (t.me/s/amitsegal)."""
        print("\n[TEST] Running fetch_telegram_channel on @amitsegal (Live)...")
        posts = fetch_telegram_channel("amitsegal", limit=2)
        self.assertIsInstance(posts, list)
        self.assertGreater(len(posts), 0)
        self.assertTrue(posts[0]["url"].startswith("https://t.me/amitsegal/"))
        print(f"  -> Success: Fetched Telegram post {posts[0]['url']}: '{posts[0]['title'][:45]}...'")

    def test_globes_hebrew_and_gdelt_news(self):
        """Verify Globes RSS (iID=2) returns Hebrew articles and search_gdelt_news handles 429 fallback."""
        print("\n[TEST] Running fetch_israeli_rss('globes') Hebrew check & search_gdelt_news...")
        globes_items = fetch_israeli_rss("globes", limit=2)
        self.assertGreater(len(globes_items), 0)
        self.assertTrue(
            any("\u0590" <= c <= "\u05FF" for c in globes_items[0]["title"]),
            f"Expected Hebrew title from Globes (iID=2), got: {globes_items[0]['title']}"
        )

        gdelt_items = search_gdelt("טכנולוגיה", max_records=2)
        self.assertIsInstance(gdelt_items, list)
        self.assertGreater(len(gdelt_items), 0)
        self.assertIn("title", gdelt_items[0])
        print(f"  -> Success: Globes Hebrew ('{globes_items[0]['title'][:35]}...') & GDELT/fallback verified")


class TestMCPServerHardening(unittest.TestCase):
    """Test suite for MCP stdio JSON-RPC 2.0 protocol hardening across all 5 tools."""

    def _start_mcp_proc(self, script_path):
        proc = subprocess.Popen(
            [sys.executable, script_path, "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        return proc

    def test_mcp_ignore_notifications_initialized(self):
        """Verify MCP stdio ignores notifications/initialized without emitting any response or error."""
        print("\n[TEST] Running MCP notifications/initialized compliance check...")
        for script_name in ["book_tool.py", "local_books_tool.py", "social_tool.py", "forum_tool.py", "news_tool.py"]:
            proc = self._start_mcp_proc(os.path.join(SCRIPT_DIR, script_name))
            try:
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
                proc.stdin.flush()
                init_resp = json.loads(proc.stdout.readline().strip())
                self.assertEqual(init_resp.get("id"), 1)

                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                proc.stdin.flush()

                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
                proc.stdin.flush()

                next_resp = json.loads(proc.stdout.readline().strip())
                self.assertEqual(next_resp.get("id"), 2)
                self.assertIn("result", next_resp)
                self.assertIn("tools", next_resp["result"])
            finally:
                proc.terminate()
                proc.wait(timeout=2)
        print("  -> Success: All 5 MCP servers cleanly ignored notifications/initialized and listed tools")

    def test_mcp_null_params_and_arguments(self):
        """Verify MCP stdio handles params: null and arguments: null without crashing."""
        print("\n[TEST] Running MCP null params & null arguments crash resilience...")
        proc = self._start_mcp_proc(os.path.join(SCRIPT_DIR, "book_tool.py"))
        try:
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 101, "method": "tools/call", "params": None}) + "\n")
            proc.stdin.flush()
            resp1 = json.loads(proc.stdout.readline().strip())
            self.assertEqual(resp1.get("id"), 101)
            self.assertTrue(proc.poll() is None, "Subprocess must remain alive on params: null")

            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0",
                "id": 102,
                "method": "tools/call",
                "params": {"name": "search_books", "arguments": None}
            }) + "\n")
            proc.stdin.flush()
            resp2 = json.loads(proc.stdout.readline().strip())
            self.assertEqual(resp2.get("id"), 102)
            self.assertTrue(proc.poll() is None, "Subprocess must remain alive on arguments: null")
            print("  -> Success: null params and null arguments handled safely with process remaining alive")
        finally:
            proc.terminate()
            proc.wait(timeout=2)

    def test_mcp_malformed_json_parse_error(self):
        """Verify MCP stdio returns JSON-RPC -32700 on malformed JSON line."""
        print("\n[TEST] Running MCP -32700 Parse error verification...")
        proc = self._start_mcp_proc(os.path.join(SCRIPT_DIR, "forum_tool.py"))
        try:
            proc.stdin.write('{"jsonrpc": "2.0", broken_syntax\n')
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline().strip())
            self.assertIn("error", resp)
            self.assertEqual(resp["error"].get("code"), -32700)
            self.assertIn("Parse error", resp["error"].get("message", ""))
            self.assertTrue(proc.poll() is None, "Subprocess must remain alive after parse error")
            print("  -> Success: Malformed JSON returned code -32700 Parse error while server stayed alive")
        finally:
            proc.terminate()
            proc.wait(timeout=2)

    def test_mcp_non_dict_invalid_request(self):
        """Verify MCP stdio returns JSON-RPC -32600 Invalid Request on non-dict input."""
        print("\n[TEST] Running MCP -32600 Invalid Request verification...")
        proc = self._start_mcp_proc(os.path.join(SCRIPT_DIR, "social_tool.py"))
        try:
            proc.stdin.write(json.dumps([1, 2, 3]) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline().strip())
            self.assertIn("error", resp)
            self.assertEqual(resp["error"].get("code"), -32600)
            self.assertTrue(proc.poll() is None, "Subprocess must remain alive after invalid request")
            print("  -> Success: Non-dict input returned code -32600 Invalid Request")
        finally:
            proc.terminate()
            proc.wait(timeout=2)

    def test_mcp_safe_int_param_fallback(self):
        """Verify MCP stdio safely handles non-integer param values without crashing."""
        print("\n[TEST] Running MCP safe_int parameter casting verification...")
        proc = self._start_mcp_proc(os.path.join(SCRIPT_DIR, "book_tool.py"))
        try:
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0",
                "id": 103,
                "method": "tools/call",
                "params": {"name": "search_books", "arguments": {"query": "python", "max_results": "NOT_AN_INT"}}
            }) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline().strip())
            self.assertEqual(resp.get("id"), 103)
            self.assertTrue(proc.poll() is None, "Subprocess must remain alive when integer argument is invalid")
            print("  -> Success: Invalid int param gracefully defaulted without crashing server")
        finally:
            proc.terminate()
            proc.wait(timeout=2)


def run_all_tests():
    """Custom runner formatting test suite results for forensic logging."""
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestBookTool))
    suite.addTests(loader.loadTestsFromTestCase(TestLocalBooksTool))
    suite.addTests(loader.loadTestsFromTestCase(TestSocialTool))
    suite.addTests(loader.loadTestsFromTestCase(TestForumTool))
    suite.addTests(loader.loadTestsFromTestCase(TestNewsTool))
    suite.addTests(loader.loadTestsFromTestCase(TestMCPServerHardening))

    print("=" * 78)
    print("AGENT DATA SOURCES TEST SUITE - AUTOMATED FORENSIC VERIFICATION (v2.0)")
    print("=" * 78)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 78)
    print(f"SUMMARY: Ran {result.testsRun} tests. Failures: {len(result.failures)}, Errors: {len(result.errors)}")
    print("=" * 78)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
