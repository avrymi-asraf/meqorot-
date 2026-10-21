# Agent Data Sources: Multi-Domain Retrieval Suite (v2.0)

Production-grade information retrieval tools, clients, and Model Context Protocol (MCP) servers connecting autonomous AI agents to global and Hebrew data repositories across five core domains:
1. **Books (R1)**: OpenLibrary, Google Books, HebrewBooks.org (`api.ashx`, `suggest.ashx`, `shas.aspx`, PDF streaming), Sefaria.org (`/api/name`, `/api/texts`, `/api/search-wrapper`), and Internet Archive OCR (`_djvu.txt`).
2. **Local Book Library Indexer (R1b)**: Fast local PDF/EPUB/TXT/MD indexing and search (`tools/local_books_tool.py`) powered by PyMuPDF + SQLite FTS5 (`unicode61` + `trigram`), featuring Hebrew nikkud stripping, legacy visual-RTL PDF repair (`0xE0..0xFA` -> logical UTF-8), and Hebrew prefix/sofit query expansion.
3. **Social Networks & Personal Accounts (R2)**: X.com / Twitter (SocialData.tools & personal `X_AUTH_TOKEN`/`X_CT0` cookies for Bookmarks/Timeline), Instagram (Apify & personal `IG_SESSIONID` cookie for Saved/Feed), Facebook (Apify), and Exa.ai neural search.
4. **Discussion Forums (R3)**: Reddit (keyless Arctic Shift archive with Hebrew `r/Israel` fallback & bounded `after` date), Rotter.net Scoops (mandatory `windows-1255` decoding), Discourse REST, and Google PSE / SerpApi / custom SERP dorking.
5. **Real-Time News & Media (R4)**: Google News RSS (`IL:he` & `US:en`), 12 Israeli Hebrew RSS feeds (Ynet, Haaretz, Israel Hayom, Maariv, Walla, Globes, TheMarker, Kan 11, C14, Arutz 7, Bhol, Mako/N12 with explicit UTF-8 byte decoding), Telegram public channel web previews (`t.me/s/`), and GDELT 2.0 DOC API.

---

## 1. File Organization

```
/home/avreymi/teamwork_projects/agent_data_sources/
├── tools/
│   ├── __init__.py           # Package marker exposing all 5 domain clients
│   ├── book_tool.py          # Global & Hebrew books (OpenLibrary, Google Books, HebrewBooks, Sefaria, IA)
│   ├── local_books_tool.py   # Local PDF/EPUB/TXT/MD FTS5 indexer & Hebrew morphological search engine
│   ├── social_tool.py        # Social media & personal X/Instagram account client
│   ├── forum_tool.py         # Forum search (Arctic Shift, Rotter RSS, Discourse, SERP Dorking)
│   ├── news_tool.py          # News engine (Google News RSS, 12 Israeli RSS feeds, Telegram, GDELT)
│   └── test_tools.py         # Automated forensic test suite (unit + live integration tests)
├── AGENT_DATA_SOURCES_GUIDE.md # Compact master blueprint (Books, Local, Social, Forums, News, WSL Browser)
├── PROJECT.md                # Architecture, feature inventory, and interface contracts
├── mcp_config.json           # Model Context Protocol server declarations (5 servers)
├── .env.example              # Documented environment variables & session cookie template
└── README.md                 # Quickstart & usage reference
```

---

## 2. Python API Usage

### Global & Hebrew Books (`tools/book_tool.py`)
```python
from tools.book_tool import (
    search_books,
    search_sefaria,
    fetch_sefaria_text,
    fetch_hebrewbooks_shas,
    fetch_ia_text,
)

# 1. Search OpenLibrary (automatically prefers non-'goog' Internet Archive scans)
books = search_books("Pride and Prejudice", source="openlibrary", max_results=2)

# 2. Search Sefaria.org full-text or reference
sefaria_hits = search_books("אימתי קורין את שמע", source="sefaria", max_results=2)
daf_text = fetch_sefaria_text("Berakhot 2a", max_chars=2000)

# 3. Fetch plain-text Talmud Bavli (Gemara, Rashi, Tosafot) from HebrewBooks
shas = fetch_hebrewbooks_shas(mesechta=1, daf=2)
print(shas["gemara"][:300])

# 4. Resolve OpenLibrary Work key (OL...W) or IA ID to OCR text or CDL status
ia_result = fetch_ia_text("OL66554W", max_chars=5000)
```

### Local Book Library Search (`tools/local_books_tool.py`)
```python
from tools.local_books_tool import index_local_books, search_local_books, get_local_book_page

# 1. Index a folder of PDFs, EPUBs, TXT, and Markdown files
stats = index_local_books("/mnt/c/Users/avrahamas/Downloads")
print(f"Indexed {stats['files_indexed']} files ({stats['total_pages_in_index']} pages)")

# 2. Search in Hebrew (automatically strips nikkud, fixes visual-RTL PDFs, and expands prefixes/sofit)
hits = search_local_books("ירושלים", limit=5)
for h in hits:
    print(f"[{h['file_name']} p.{h['page_num']}] {h['snippet']}")
```

### Social Media & Personal Accounts (`tools/social_tool.py`)
```python
from tools.social_tool import (
    search_social,
    fetch_personal_x_timeline,
    fetch_personal_instagram_feed,
)

# 1. Read your personal X/Twitter Bookmarks or Timeline (uses X_AUTH_TOKEN + X_CT0)
my_bookmarks = fetch_personal_x_timeline(mode="bookmarks", max_results=5)

# 2. Read your personal Instagram Saved posts or followed profile (uses IG_SESSIONID)
my_saved_ig = fetch_personal_instagram_feed(username="saved", max_posts=5)

# 3. Search public X, Instagram, or Facebook via SocialData / Apify / Exa
tweets = search_social('("בינה מלאכותית" OR "LLM") lang:he', platform="x", method="socialdata", max_results=5)
```

### Forums (`tools/forum_tool.py`) & News (`tools/news_tool.py`)
```python
from tools.forum_tool import search_reddit_arctic_shift, fetch_rotter_scoops, build_serp_forum_dork
from tools.news_tool import search_news, fetch_israeli_rss, fetch_telegram_channel

# 1. Keyless Reddit search (ASCII uses bounded title search; Hebrew defaults to r/Israel)
py_posts = search_reddit_arctic_shift(query="asyncio", subreddit="python", limit=3)
il_posts = search_reddit_arctic_shift(query="ישראל", limit=3)

# 2. Breaking Israeli scoops from Rotter.net (Windows-1255 decoded)
scoops = fetch_rotter_scoops(limit=5)

# 3. Real-time Israeli RSS feeds (ynet, haaretz, mako, globes, maariv, walla, c14, inn, bhol)
ynet_news = fetch_israeli_rss("ynet", limit=5)
mako_news = fetch_israeli_rss("mako", limit=5)

# 4. Keyless Telegram public channel web preview & search
tg_posts = fetch_telegram_channel("amitsegal", limit=5)
```

---

## 3. Command-Line Interface (CLI)

```bash
# Global & Hebrew Books CLI
python3 tools/book_tool.py --query "Berakhot 2a" --source sefaria --limit 2
python3 tools/book_tool.py --query "ברכות" --source hebrewbooks --limit 2

# Local Books Indexer & Search CLI
python3 tools/local_books_tool.py --index "/mnt/c/Users/avrahamas/Downloads" --query "ירושלים" --limit 3

# News CLI
python3 tools/news_tool.py --source ynet --limit 3
python3 tools/news_tool.py --source telegram:amitsegal --limit 3
python3 tools/news_tool.py --source google_news --query "בינה מלאכותית" --limit 3

# Forums CLI
python3 tools/forum_tool.py --platform rotter --limit 3
python3 tools/forum_tool.py --platform reddit --subreddit python --query asyncio --limit 2

# Social CLI
python3 tools/social_tool.py --platform x --method personal --query bookmarks --limit 3
```

---

## 4. Automated Forensic Verification Test Suite

Run the complete unit and live integration test suite:
```bash
python3 tools/test_tools.py
```
