# Project: Agent Data Sources Blueprint (v2.0)

## Architecture
The system connects autonomous AI agents (Google Antigravity, Gemini, Claude Desktop, and MCP hosts) to global, Hebrew, and personal local data repositories across five core domains:
1. **Books & Local Libraries Domain (R1)**: Global repositories (Google Books, Internet Archive, OpenLibrary, Project Gutenberg, Semantic Scholar), Hebrew repositories (HebrewBooks.org JSONP/Suggest/Shas/PDF, Sefaria.org REST/ElasticSearch, Ben-Yehuda Project, NLI), and **local personal book libraries** (`tools/local_books_tool.py` using PyMuPDF + SQLite FTS5 `unicode61` & `trigram` with nikkud stripping, legacy visual-RTL PDF repair, and Hebrew prefix/sofit expansion).
2. **Social Networks & Personal Accounts Domain (R2)**: X/Twitter, Instagram, and Facebook via managed aggregators (SocialData.tools, Apify actors, Exa neural search) and **personal authenticated account access** (WSL `chrome-devtools-mcp` persistent profile or session cookies `X_AUTH_TOKEN`+`X_CT0` and `IG_SESSIONID`).
3. **Discussion Forums Domain (R3)**: Global forums (Reddit via Arctic Shift with bounded `after` dates and Hebrew `r/Israel` fallback, Quora, StackExchange, Discourse) and Hebrew forums (Rotter.net `windows-1255` RSS, FXP via SERP dorking, Tapuz XenForo UTF-8 RSS).
4. **News & Real-Time Media Domain (R4)**: Google News RSS (`IL:he` & `US:en`), 12 Israeli Hebrew RSS feeds (Ynet, Haaretz, Israel Hayom, Maariv, Walla, Globes, TheMarker, Kan 11, C14, Arutz 7, Bhol, Mako/N12 with explicit UTF-8 decoding), Telegram public channel web previews (`t.me/s/`), and GDELT 2.0 DOC API.
5. **WSL2 Browser Automation & MCP Layer (R5)**: Dual-mode Python + hardened stdio JSON-RPC 2.0 MCP servers (`mcp_config.json`) plus WSLg Linux Chrome / Windows Chrome remote-debugging architecture.

## Code Layout
Project root: `/home/avreymi/teamwork_projects/agent_data_sources/`
```
├── AGENT_DATA_SOURCES_GUIDE.md        # Compact, high-signal master architectural guide
├── ORIGINAL_REQUEST.md                # Verbatim user request & acceptance criteria
├── PROJECT.md                         # Project index, architecture, contracts
├── README.md                          # Quickstart and overview of tools and configs
├── mcp_config.json                    # Model Context Protocol server configuration (5 servers)
├── .env.example                       # Environment variable templates & cookie guide
└── tools/
    ├── __init__.py                    # Python package marker
    ├── book_tool.py                   # Global & Hebrew book search (OpenLibrary, Google Books, HebrewBooks, Sefaria, IA)
    ├── local_books_tool.py            # Local PDF/EPUB/TXT/MD indexer & FTS5 search engine
    ├── social_tool.py                 # Social media & personal X/Instagram account client
    ├── forum_tool.py                  # Forum search (Reddit Arctic Shift, Rotter RSS, Discourse, SERP dorking)
    ├── news_tool.py                   # Real-time news (Google News RSS, 12 Israeli RSS feeds, Telegram, GDELT)
    └── test_tools.py                  # Comprehensive test suite verifying all 5 tools + MCP hardening
```

## Interface Contracts

### 1. Book Tool Contract (`tools/book_tool.py`)
- `search_books(query: str, source: str = "openlibrary", max_results: int = 5) -> List[Dict]`
  - Sources: `"openlibrary"`, `"googlebooks"`, `"hebrewbooks"`, `"sefaria"`
- `fetch_book_text(book_id: str, source: str = "openlibrary", max_chars: int = 10000) -> Dict`
  - Supports OpenLibrary IA IDs & `OL...W`/`OL...M` work/edition resolution, Sefaria references (`"Berakhot 2a"`), and HebrewBooks (`"shas:1:2"` or PDF links).

### 2. Local Books Tool Contract (`tools/local_books_tool.py`)
- `index_local_books(paths: Union[str, List[str]], db_path: str = DEFAULT_DB_PATH, force: bool = False) -> Dict`
- `search_local_books(query: str, db_path: str = DEFAULT_DB_PATH, limit: int = 10, expand_hebrew: bool = True) -> List[Dict]`
- `get_local_book_page(file_path_or_title: str, page_num: int = 1, db_path: str = DEFAULT_DB_PATH) -> Dict`

### 3. Social Tool Contract (`tools/social_tool.py`)
- `search_social(query: str, platform: str = "x", method: str = "auto", max_results: int = 10, api_key: Optional[str] = None) -> List[Dict]`
  - Standardized Return includes both `"metrics"` and `"engagement"` dicts.
- `fetch_personal_x_timeline(mode: str = "bookmarks", auth_token: Optional[str] = None, ct0: Optional[str] = None, max_results: int = 10) -> List[Dict]`
- `fetch_personal_instagram_feed(username: Optional[str] = None, sessionid: Optional[str] = None, max_posts: int = 10) -> List[Dict]`

### 4. Forum Tool Contract (`tools/forum_tool.py`)
- `search_reddit_arctic_shift(query: Optional[str] = None, subreddit: Optional[str] = None, limit: int = 10) -> List[Dict]` (alias: `search_arctic_shift`)
- `fetch_rotter_scoops(limit: int = 10) -> List[Dict]`
- `build_serp_forum_dork(query: str, domain: str = "fxp.co.il", search_engine_url: Optional[str] = None, api_key: Optional[str] = None, limit: int = 10) -> Dict`

### 5. News Tool Contract (`tools/news_tool.py`)
- `search_news(query: str = "", source: str = "google_news", lang: str = "auto", limit: int = 10) -> List[Dict]`
- `search_google_news_rss(query: str, lang: str = "auto", country: str = "IL", limit: int = 10) -> List[Dict]`
- `fetch_israeli_rss(source: str = "ynet", query: Optional[str] = None, limit: int = 10) -> List[Dict]`
- `fetch_telegram_channel(channel: str = "amitsegal", query: Optional[str] = None, limit: int = 10) -> List[Dict]`
- `search_gdelt_news(query: str, max_records: int = 10, timespan: str = "7d") -> List[Dict]`
