# Architectural Blueprint & Implementation Guide: AI Agent Data Sources

## 1. Executive Summary & Multi-Domain Architecture

Autonomous AI agents require structured, anti-bot-resilient access across five core knowledge domains:
1. **Books & Local Libraries (R1)**: Global catalogs (OpenLibrary, Google Books, Gutenberg, Semantic Scholar), Hebrew/Judaic vaults (HebrewBooks.org, Sefaria, Ben-Yehuda, NLI), and **local personal book libraries** (PDF/EPUB/TXT/MD indexed via PyMuPDF + SQLite FTS5).
2. **Social Networks & Personal Accounts (R2)**: X.com/Twitter, Instagram, and Facebook via managed aggregators (SocialData.tools, Apify, Exa.ai) and **authenticated personal account readers** (browser profile MCP or session cookies `X_AUTH_TOKEN`/`X_CT0` & `IG_SESSIONID`).
3. **Discussion Forums (R3)**: Reddit (Arctic Shift shadow archive), StackExchange (`stackoverflow`, `judaism`), Discourse, Rotter.net (`windows-1255` RSS), Tapuz (XenForo RSS), and Cloudflare-protected boards (FXP, Quora via SERP Dorking).
4. **News & Real-Time Media (R4)**: Google News RSS (`IL:he` & `US:en`), 12 Israeli Hebrew news RSS portals, Telegram public channel web previews (`t.me/s/`), and the GDELT 2.0 Global Knowledge Graph.
5. **WSL2 Browser Automation (R5)**: Solving the WSL2 + Antigravity browser bridge via WSLg Linux Chrome (`chrome-devtools-mcp`), Windows Chrome `156.0.7073.0` with isolated `--user-data-dir` + `netsh interface portproxy`, or Patchright/Playwright.

```
                        ┌────────────────────────────────────────────────────────┐
                        │                   AI AGENT LAYER                       │
                        │       (Google Antigravity / Gemini / Claude)           │
                        └───────────┬────────────────────────────┬───────────────┘
                                    │ JSON-RPC (stdio MCP)       │ Python API
                                    ▼                            ▼
                        ┌─────────────────────────┐  ┌───────────────────────────┐
                        │   MCP SERVER SUITE      │  │    PYTHON TOOL SUITE      │
                        │   (mcp_config.json)     │  │    (tools/*.py)           │
                        └───────────┬─────────────┘  └───────────┬───────────────┘
                                    │                            │
      ┌────────────────────┬────────┴───────────┬────────────────┼────────────────────┐
      ▼                    ▼                    ▼                ▼                    ▼
┌───────────────┐  ┌───────────────┐  ┌─────────────────┐ ┌───────────────┐  ┌────────────────┐
│ BOOKS & LOCAL │  │ SOCIAL ENGINE │  │  FORUMS ENGINE  │ │  NEWS ENGINE  │  │  WSL BROWSER   │
├───────────────┤  ├───────────────┤  ├─────────────────┤ ├───────────────┤  ├────────────────┤
│• OpenLibrary  │  │• SocialData(X)│  │• Arctic Shift   │ │• Google News  │  │• WSLg Chrome   │
│• Google Books │  │• Apify (IG/FB)│  │• Rotter (1255)  │ │• 12 IL Feeds  │  │• DevTools MCP  │
│• HebrewBooks  │  │• Exa Neural   │  │• Discourse REST │ │  (Ynet/Mako…) │  │• Windows NAT   │
│• Sefaria API  │  │• Personal X & │  │• StackExchange  │ │• Telegram Web │  │  Portproxy     │
│• Local FTS5   │  │  IG Cookies   │  │• SERP Dorking   │ │• GDELT 2.0 API│  │• Auth Profiles │
└───────────────┘  └───────────────┘  └─────────────────┘ └───────────────┘  └────────────────┘
```

### 1.1 Metadata Discovery vs. Full-Text Ingestion
- **Tier 1 — Catalog Metadata (<20 KB, <250 ms)**: Queries OpenLibrary, Google Books, or NLI for existence, ISBN, year, and rights flags (`publicDomain`, `ebook_access`). Fits 20–50 records in context.
- **Tier 2 — Full-Text Retrieval & Local FTS5 Chunking (50 KB–50 MB)**: Streams public-domain OCR (`_djvu.txt`), Sefaria JSON segments, HebrewBooks PDFs/Talmud text, or local PDF pages -> strips Hebrew diacritics (nikkud) -> repairs legacy visual-RTL encodings -> retrieves top-K passages (500–1,000 tokens) via BM25/FTS5 or dense vectors.

---

## 2. Books & Local Book Library Architecture (`tools/book_tool.py`, `tools/local_books_tool.py`)

### 2.1 Global Book Repositories
1. **OpenLibrary & Internet Archive (`search_openlibrary`, `fetch_ia_text`)**:
   - **Search**: `GET https://openlibrary.org/search.json?q={query}&fields=key,title,author_name,first_publish_year,isbn,ia,has_fulltext,ebook_access`
   - **Critical IA ID Selection**: OpenLibrary works often list Google-digitized scans ending in `goog` (`bub_gb_...`) first, which lack `_djvu.txt` or block downloads. Always prefer non-`goog` identifiers: `next((x for x in ia_ids if not x.endswith("goog")), ia_ids[0])`.
   - **Work/Edition Key Resolution**: When given an OpenLibrary key (`OL...W` or `OL...M`), `_resolve_openlibrary_ia_candidates` resolves `ocaid`, `source_records` (`ia:...`), and parent `/works/OL...W` `ia` lists, iterating candidate items until a public-domain `_djvu.txt` or `.txt` is retrieved.
   - **CDL Post-*Hachette* Boundary**: Items with `access-restricted-item: true` or `ebook_access: "borrowable"` are locked under Controlled Digital Lending and return HTTP 401/403 on raw text downloads. Detect this in `/metadata/{ia_id}` and report CDL status cleanly.
2. **Google Books API v1 (`search_google_books`)**:
   - **Endpoint**: `GET https://www.googleapis.com/books/v1/volumes?q={query}&projection=full&key={GOOGLE_BOOKS_API_KEY}`
   - **Operators**: `intitle:`, `inauthor:`, `isbn:`, `subject:`, `langRestrict=he`, `filter=free-ebooks`.
   - **Quota Reality**: Unauthenticated requests share a throttled cloud pool and return `HTTP 429`. Supply `GOOGLE_BOOKS_API_KEY` (1,000 free requests/day). Calls with `limit <= 0` short-circuit immediately before any HTTP request.
3. **Project Gutenberg (`Gutendex`) & Semantic Scholar (`S2AG`)**:
   - **Gutendex**: `GET https://gutendex.com/books/?search={query}` -> full text at `https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt`.
   - **Semantic Scholar**: `GET https://api.semanticscholar.org/graph/v1/paper/search?query={query}&publicationTypes=Book,BookSection&fields=title,authors,year,isOpenAccess,openAccessPdf`. Free API key (`SEMANTIC_SCHOLAR_API_KEY`) raises limit from 1 req/s to 10 req/s.

### 2.2 Hebrew & Judaic Book Repositories
1. **HebrewBooks.org (`search_hebrewbooks`, `suggest_hebrewbooks`, `fetch_hebrewbooks_shas`, `download_hebrewbooks_pdf`)**:
   - **Catalog Search (JSONP)**: `GET https://beta.hebrewbooks.org/api/api.ashx?title_search={query}&start=0&length={limit}&callback=bot` (returns `bot();` when empty).
   - **Autocomplete Suggest**: `GET https://beta.hebrewbooks.org/suggest/suggest.ashx?json=1&autosuggest=1&limit=10&src=title&q={query}`.
   - **Full PDF Stream**: `GET https://download.hebrewbooks.org/downloadhandler.ashx?req={book_id}` (validates `%PDF` magic bytes to reject HTML error pages).
   - **Plain-Text Shas (Talmud Bavli)**: `GET https://hebrewbooks.org/shas.aspx?mesechta={1..37}&daf={2..}&format=text` extracts structured UTF-8 text for Gemara (`div.shastext2`), Rashi (`div.shastext3`), and Tosafot (`div.shastext4`). Accessible via `fetch_hebrewbooks_shas(1, "2a")` or `fetch_book_text("shas:1:2", source="hebrewbooks")`.
   - **WAF Requirement**: Requires browser `User-Agent` and `Referer: https://beta.hebrewbooks.org/`.
2. **Sefaria.org (`search_sefaria`, `fetch_sefaria_text`)**:
   - **Reference Resolution**: `GET https://www.sefaria.org/api/name/{query}` resolves titles and exact citations (`is_ref: true`, `ref: "Berakhot 2a"`).
   - **Bilingual Text Extraction**: `GET https://www.sefaria.org/api/texts/{ref}?context=0` returns full vocalized Hebrew (`he`) and English (`text`) arrays with zero rate-limit friction.
   - **Full-Text Search**: `POST https://www.sefaria.org/api/search-wrapper` with `{"query": query, "type": "text", "size": limit}` searches the entire Torah/Talmud/Midrash/Halakhah corpus with highlighted snippets.
3. **Project Ben-Yehuda & National Library of Israel (NLI)**:
   - **Ben-Yehuda**: `POST https://benyehuda.org/api/v1/search` or GitHub dump `projectbenyehuda/public_domain_dump` (`txt_stripped/` directory pre-strips nikkud, reducing Hebrew LLM token usage by 35–50%).
   - **NLI**: `https://api.nli.org.il/openlibrary/search` (requires registered `NLI_API_KEY` + browser `User-Agent`; includes the RAMBI academic articles index).

### 2.3 Searching Your Own Huge Local Book Collection (`tools/local_books_tool.py`)
To search thousands of local PDFs, EPUBs, TXT, and Markdown books on WSL/Windows (`/mnt/c/Users/avrahamas/...`), use the two-tier local architecture implemented in [`tools/local_books_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/local_books_tool.py):
1. **Tier 1 — Fast Lexical & Morphological Index (`PyMuPDF` + SQLite FTS5)**:
   - Extracts pages at ~50–200 pages/sec using `pymupdf` (`fitz`), plus native ZIP/XHTML parsing for `.epub` and chunked `.txt`/`.md`. Tracks both `mtime` and `file_size` in `books_meta` for incremental re-indexing.
   - **Nikkud Stripping (`strip_nikkud`)**: Removes combining marks `U+0591..U+05C7` and PDF record separators (`\x1e`) so vocalized books (e.g. `סֵפֶר מֹורְמֹון` / `וְנֶפִי`) match unvocalized queries (`נפי`).
   - **Legacy Visual-RTL Hebrew PDF Repair (`fix_legacy_hebrew_pdf`)**: Many Israeli PDFs store Hebrew as visual-RTL `Windows-1255` bytes mapped into `Latin-1` (`0xE0..0xFA`, e.g. `íééç` for `חיים`, `ééàìéáåî` for `מובילאיי`) or PostScript `/CE0../CFA` glyph codes. `fix_legacy_hebrew_pdf` detects high-byte Latin-1 Hebrew runs, transcodes `0xE0..0xFA -> U+05D0..U+05EA`, and reverses visual-RTL runs into logical UTF-8 order before indexing.
   - **Hebrew Prefix, Sofit & Gershayim FTS5 Expansion (`expand_hebrew_word_fts5`)**:
     - Expands each regular Hebrew query word across proclitic prefixes (`ה, ו, ב, ל, מ, כ, ש, וה, וב, שה...`) and final-to-medial letter transitions (`SOFIT_TO_MEDIAL`: `ך->כ, ם->מ, ן->נ, ף->פ, ץ->צ`) so searching `מלך` matches `המלך` and `מלכים`, and searching `ירושלים` matches `בירושלים`.
     - **Hebrew Abbreviations with Gershayim (`בע"מ`, `דו"ח`, `רמב"ם`)**: Because SQLite FTS5 `unicode61` treats `"` as a token separator (splitting `בע"מ` into `"בע"` + `"מ"`), and some Hebrew PDFs invert runs around neutral `"` (`מ"בע` in `פוליסה AIG.pdf`), `expand_hebrew_word_fts5` generates exact adjacent phrase tokens (`"בע מ"`), visual-RTL quote-inverted tokens (`"מ בע"`), and unquoted forms (`"בעמ"`) without wildcard `*`—preventing false positives like `בעמודת` or `מדוחי`.
   - **Dual FTS5 Tables**: Combines `unicode61 remove_diacritics 2` (BM25 ranked search) with a `trigram` FTS5 table (with escaped internal double quotes `""`) for substring fallback.
2. **Tier 2 — Optional Semantic RAG & Desktop CLI Accelerators**:
   - For conceptual cross-lingual search ("find passages discussing moral agency" across Hebrew and English books), embed page chunks with **`BAAI/bge-m3`** or **`intfloat/multilingual-e5-large`** into **LanceDB** or **Qdrant** alongside FTS5 (Reciprocal Rank Fusion).
   - For zero-code CLI search over unindexed folders: **`rga` (ripgrep-all)** searches inside PDFs/EPUBs/ZIPs on the fly; **Calibre (`calibredb fts_search`)** manages EPUB/MOBI libraries.

### 2.4 Books & Local Library Comparison Matrix
| Source / Tool | Access Method | Cost | Rate Limits | Anti-Bot | Hebrew Support | Score |
|---|---|---|---|---|---|---|
| **Local Books FTS5 (`local_books_tool.py`)** | PyMuPDF + SQLite FTS5 (`unicode61` + `trigram`) | Free ($0) | Unlimited (local disk) | **None** | **Native** (Nikkud strip + Visual-RTL repair + Sofit/Prefix/Gershayim) | **10/10** |
| **Sefaria.org API** | REST (`/api/name`, `/api/texts`, `/api/search-wrapper`) | Free ($0) | High polite quota | **None** | **Native** (100% vocalized Hebrew + English translation) | **10/10** |
| **Ben-Yehuda Project** | REST API + GitHub (`txt_stripped/`) | Free ($0) | Unlimited via GitHub | **None** | **Native** (Pre-stripped nikkud saves 35–50% tokens) | **10/10** |
| **Google Books API** | REST (`/books/v1/volumes`) | Free w/ Key | 1,000 req/day (`GOOGLE_BOOKS_API_KEY`) | **Low** | **Native** (`langRestrict=he`, snippet-only for in-copyright) | **9/10** |
| **OpenLibrary + IA** | REST (`/search.json`) + `_djvu.txt` | Free ($0) | ~1–2 req/sec | **Low** (CDL returns 403) | **Native** (Prefer non-`goog` IA IDs; resolve `OL...W`/`OL...M`) | **8.5/10** |
| **HebrewBooks.org** | JSONP (`api.ashx`), `suggest.ashx`, `shas.aspx`, PDF | Free ($0) | Polite usage | **Moderate** (Browser UA + Referer required) | **Native** (65,000+ rabbinic PDFs + plain-text Shas) | **8.5/10** |
| **Semantic Scholar** | REST (`/graph/v1/paper/search`) | Free w/ Key | 1 req/s (unauth), 10 req/s (key) | **Low** | **Partial** (Academic metadata + OpenAccess PDFs) | **9/10** |

---

## 3. Social Networks & Reading Personal Accounts (`tools/social_tool.py`)

### 3.1 Public Social Search: Official APIs vs. Managed Aggregators vs. Scrapers
- **X.com (Twitter)**: Official X API v2 charges **$5.00 per 1,000 tweets** ($0.005/read) or $42,000/mo Enterprise. **SocialData.tools** (`SOCIALDATA_API_KEY`) provides full Twitter Advanced Search (`lang:he`, `min_faves:`, `from:`, `conversation_id:`) at **$0.20 per 1,000 tweets** (25x cheaper).
- **Instagram & Facebook**: Meta shut down CrowdTangle (Aug 2024) and gated the Meta Content Library (MCL) behind academic ICPSR enclaves. Public Graph API offers zero arbitrary post search. Use **Apify Actors** (`apify~instagram-scraper` and `apify~facebook-posts-scraper` via `APIFY_API_TOKEN`) or **Exa.ai Neural Search** (`EXA_API_KEY` with `includeDomains: ["x.com", "instagram.com", "facebook.com"]`).
- **Unified Schema Contract**: Every `SocialPost.to_dict()` includes both `"metrics"` and `"engagement"` keys (`{"likes": ..., "retweets": ..., "replies": ...}`) for backward and forward compatibility.
- **Dead Scrapers Autopsy**: `snscrape` is permanently dead (Twitter killed unauthenticated guest tokens in July 2023 and added Kasada Proof-of-Work `x-kpsdk-*` headers). `facebook-scraper` is permanently dead (Meta shut down `mbasic.facebook.com` in 2024).

### 3.2 How the Agent Can Read Your Personal X/Twitter & Instagram Accounts
When you want the agent to read **your own feeds, saved Bookmarks, private lists, or followed private accounts**, there are three architectural patterns:

1. **Pattern A (Recommended on WSL): Persistent Logged-In Browser via `chrome-devtools-mcp`**:
   - Launch Linux Chrome on WSLg with a dedicated persistent profile directory (`--user-data-dir=$HOME/.config/chrome-agent-profile`).
   - Log into your X.com and Instagram accounts once manually inside that Chrome window.
   - Because the agent drives your real Chrome binary via `chrome-devtools-mcp`, Kasada PoW, WebGL/Canvas fingerprints, TLS JA3/JA4 handshakes, and residential IP affinity are 100% genuine. The agent can navigate to `https://x.com/i/bookmarks`, `https://x.com/home`, or `https://www.instagram.com/saved/` and read the live DOM Snapshot (`take_snapshot`) with zero ban risk.
2. **Pattern B: Session Cookie Injection in `tools/social_tool.py` (`fetch_personal_x_timeline`, `fetch_personal_instagram_feed`)**:
   - **For X.com (`X_AUTH_TOKEN` + `X_CT0`)**:
     1. Log into `https://x.com` in your browser -> Press `F12` (DevTools) -> **Application** -> **Cookies** -> `https://x.com`.
     2. Copy `auth_token` and `ct0` into `.env` as `X_AUTH_TOKEN` and `X_CT0`.
     3. Call `fetch_personal_x_timeline(mode="bookmarks")` (or `mode="timeline"` / `mode="user_tweets"`, which automatically falls back to v1.1 `statuses/home_timeline.json` if GraphQL `queryId` rotates) or `search_social("bookmarks", platform="x")`.
   - **For Instagram (`IG_SESSIONID`)**:
     1. Log into `https://www.instagram.com` -> DevTools (`F12`) -> **Application** -> **Cookies** -> copy `sessionid`.
     2. Set `IG_SESSIONID` in `.env` and call `fetch_personal_instagram_feed(username="saved")` (for saved posts), `fetch_personal_instagram_feed(username="feed")` (for home timeline `/api/v1/feed/timeline/`), or `fetch_personal_instagram_feed(username="target_handle")`.
   - *Operational Safeguard*: Only use your personal session cookies from your own residential IP (WSL2 shares your home ISP IP) and keep polling rates low (<10 requests/hour) to avoid triggering `checkpoint_required`.
3. **Pattern C: Official Account Archive Export + Local FTS5 Index**:
   - Request your official data archive from X (*Settings -> Your Account -> Download an archive of your data*) and Instagram (*Accounts Center -> Your information and permissions -> Download your information* in JSON format).
   - Point `index_local_books` at the extracted archive folder for instant, zero-latency offline search across your entire lifetime history of tweets, likes, bookmarks, and DMs.

### 3.3 Social Networks Comparison Matrix
| Source / Method | Target | Cost | Anti-Bot & Maintenance | Hebrew Support | Score |
|---|---|---|---|---|---|
| **WSL Chrome Profile (`chrome-devtools-mcp`)** | Personal X + IG (Bookmarks, Feed, DMs) | Free ($0) | **None** (Real browser TLS/JS + home IP) | **Native** (Full RTL DOM rendering) | **9.5/10** |
| **Personal Cookies (`social_tool.py --method personal`)** | Personal X (`X_AUTH_TOKEN`/`X_CT0`) & IG (`IG_SESSIONID`) | Free ($0) | **Moderate** (Cookies rotate every few weeks; keep rate low) | **Native** (Full UTF-8 JSON) | **8.5/10** |
| **SocialData.tools (`SOCIALDATA_API_KEY`)** | Public X.com Search & Threads | $0.20 / 1k tweets | **Managed** (25x cheaper than X API v2) | **High** (`lang:he` + prefix disjunctions) | **9.0/10** |
| **Exa.ai Neural Search (`EXA_API_KEY`)** | Public X, Instagram, Facebook | ~$0.005 / query | **None** (Queries Exa's crawled index) | **Very High** (Semantic Hebrew embeddings) | **8.8/10** |
| **Apify Actors (`APIFY_API_TOKEN`)** | Public IG Profiles/Posts & FB Pages | $1.50–$5.00 / 1k | **Managed** (Residential proxies included) | **High** (Full UTF-8 captions/posts) | **8.2/10** |
| **`snscrape` / `facebook-scraper`** | Legacy Unauth Scrapers | Free | **Dead (0% throughput)** | **None** | **0.0/10** |

---

## 4. Discussion Forums & Community Boards (`tools/forum_tool.py`)

### 4.1 Global & Hebrew Forum Engines
1. **Reddit via Arctic Shift (`search_reddit_arctic_shift` / `search_arctic_shift` / `fetch_reddit_arctic_shift`)**:
   - **Endpoint**: `GET https://arctic-shift.photon-reddit.com/api/posts/search` (keyless public shadow archive).
   - **Critical Query Rules Verified Live**:
     - Arctic Shift's PostgreSQL FTS index uses `title={query}` (not `query=`), and un-bounded `title` searches across large subreddits time out with `HTTP 422`. Always bound ASCII `title` searches with `after={YYYY-MM-DD}` (e.g., 180 days ago).
     - **Hebrew Queries (`[\u0590-\u05FF]`) & Multi-Word Queries**: Arctic Shift's English FTS dictionary times out on Hebrew characters. For Hebrew queries without an explicit subreddit, default to `subreddit="Israel"` (and default no-arg calls to `"Python"`), fetch `limit=100` recent posts without `title=`, and filter client-side using both exact substring and multi-word token conjunction (`all(t in haystack for t in q_tokens)`), unconditionally setting `posts = matched[:limit]`.
     - Normalize canonical subreddit casing (`{"israel": "Israel", "askreddit": "AskReddit", "palestine": "Palestine", "judaism": "Judaism"}`).
2. **Rotter.net Scoops RSS (`fetch_rotter_scoops`)**:
   - **Endpoint**: `GET https://rotter.net/rss/rotternews.xml` (bypasses Cloudflare Turnstile on `/forum/scoops1/`).
   - **Mandatory `windows-1255` Decoding**: Rotter encodes Hebrew in legacy `windows-1255`. Decoding with UTF-8 raises `UnicodeDecodeError` (`0xf8`), and `resp.text` produces `ISO-8859-1` mojibake. Always decode raw bytes via `resp.content.decode("windows-1255", errors="replace")`.
3. **Discourse (`search_discourse_forum`), StackExchange (`search_stackexchange`), & Tapuz XenForo RSS**:
   - Any Discourse forum (`discuss.python.org`, `discuss.huggingface.co`) exposes keyless `GET /search.json?q={query}` and `npx -y @discourse/mcp@latest`.
   - **StackExchange API v2.3 (`search_stackexchange` / `fetch_stackexchange`)**: Queries `GET https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance&q={query}&site={site}&filter=withbody` across `stackoverflow`, `serverfault`, `judaism` (Mi Yodeya), etc., stripping HTML from answer/question bodies and returning up to 300 keyless requests/day per IP.
   - Tapuz (`tapuz.co.il`) runs XenForo 2.x with open UTF-8 RSS feeds at `https://www.tapuz.co.il/forums/-/index.rss`.
4. **Cloudflare-Protected Forums via SERP Dorking (`build_serp_forum_dork`, `search_forum_serp_dork`)**:
   - Boards like **FXP (`fxp.co.il`)** and **Quora (`quora.com`)** enforce Cloudflare Turnstile / login walls. Bypass them via `site:fxp.co.il {query}` executed through:
     - **Google Custom Search (`GOOGLE_PSE_KEY` + `GOOGLE_PSE_CX`)**: `https://www.googleapis.com/customsearch/v1` (100 free queries/day).
     - **SerpApi (`SERPAPI_API_KEY`)**: `https://serpapi.com/search.json`.
     - **Custom / Self-Hosted SearXNG (`search_engine_url`)**: `GET {url}?q={dork}&format=json`.

### 4.2 Forums Comparison Matrix
| Forum / Source | Access Method | Cost | Rate Limits | Anti-Bot | Hebrew Support | Score |
|---|---|---|---|---|---|---|
| **Reddit Arctic Shift** | REST (`arctic-shift.photon-reddit.com`) | Free ($0) | ~30–60 req/min | **None** | **Good** (Fetch `r/Israel` recent + token filter) | **9.0/10** |
| **Rotter.net Scoops RSS** | XML RSS (`rotter.net/rss/rotternews.xml`) | Free ($0) | Poll every 1–3m | **None** on RSS | **Legacy** (`windows-1255` byte decoding required) | **9.0/10** |
| **Tapuz.co.il RSS** | XenForo XML (`/forums/-/index.rss`) | Free ($0) | Poll every 1–5m | **None** on RSS | **Native UTF-8** (Full body in `<content:encoded>`) | **9.5/10** |
| **FXP / Quora via SERP** | Google PSE / SerpApi (`site:fxp.co.il`) | Free 100/d | Provider quota | **None** (Google cache) | **Native** (Google Hebrew lemmatizer) | **9.0/10** |
| **Discourse / StackExchange** | REST (`/search.json`, `api.stackexchange.com`) | Free ($0) | 300–10k req/day | **Low** | **Native UTF-8** (`judaism` / `stackoverflow`) | **9.5/10** |

---

## 5. News & Real-Time Media Architecture (`tools/news_tool.py`)

Real-time news intelligence across global and Israeli Hebrew sources is implemented in [`tools/news_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/news_tool.py) without requiring paid API keys:

1. **Google News RSS Search (`search_google_news_rss`)**:
   - **Endpoint**: `GET https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}`
   - Automatically switches between **Israeli Hebrew** (`hl=he&gl=IL&ceid=IL:he`) and **Global English** (`hl=en-US&gl=US&ceid=US:en`). Supports `site:ynet.co.il`, `when:24h`, and boolean queries.
2. **12 Verified Israeli Hebrew News RSS Portals (`fetch_israeli_rss`)**:
   - **Critical UTF-8 Decoding Fix**: Mako/N12 serves RSS with `Content-Type: text/xml` (omitting `charset=utf-8`), causing `requests` (`resp.text`) to default to `ISO-8859-1` per RFC 2616 and corrupting Hebrew into mojibake. `news_tool.py` explicitly decodes `resp.content.decode("utf-8", errors="replace")`.
   - **Verified Israeli Feed Endpoints (`ISRAELI_RSS_FEEDS`)**:

| Portal Key | Outlet Name | Verified Feed URL | Notes |
|---|---|---|---|
| `ynet` / `ynet_main` | Ynet (ידיעות אחרונות) | `https://www.ynet.co.il/Integration/StoryRss1854.xml` | Breaking news (`StoryRss2.xml` for top stories) |
| `haaretz` | Haaretz (הארץ) | `https://www.haaretz.co.il/srv/rss---feedly` | Full Hebrew headlines & summaries |
| `israelhayom` | Israel Hayom (ישראל היום) | `https://www.israelhayom.co.il/rss.xml` | Direct Hebrew XML feed |
| `maariv` | Maariv (מעריב) | `https://www.maariv.co.il/Rss/RssFeedsMivzakiChadashot` | Real-time Hebrew news flashes |
| `walla` | Walla News (וואלה!) | `https://rss.walla.co.il/feed/22` | Main news flash feed (`feed/1` for homepage) |
| `globes` | Globes (גלובס) | `https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2` | Hebrew homepage (`iID=1725` is `globes_en`) |
| `themarker` | TheMarker (דה-מרקר) | `https://www.themarker.com/srv/tm-hp-rss` | Israeli financial & tech news |
| `kan` | Kan 11 (כאן חדשות) | `https://news.google.com/rss/search?q=site:kan.org.il/content/kan-news&hl=he&gl=IL&ceid=IL:he` | Scoped to `/content/kan-news` Hebrew articles |
| `c14` | Channel 14 (עכשיו 14) | `https://www.c14.co.il/feed/` | WordPress UTF-8 RSS feed |
| `inn` | Arutz 7 (ערוץ 7) | `https://www.inn.co.il/Rss.aspx` | Breaking Hebrew news feed |
| `bhol` | Behadrei Haredim (בחדרי חרדים) | `https://www.bhol.co.il/rss/index.xml` | Haredi news sector RSS |
| `mako` / `n12` | Mako / N12 (מאקו / הבוקר) | `https://rcs.mako.co.il/rss/news-military.xml` | Requires explicit `resp.content.decode('utf-8')` |

3. **Telegram Public Channel Web Preview (`fetch_telegram_channel`)**:
   - **Endpoint**: `GET https://t.me/s/{channel}?q={query}` (e.g. `amitsegal`, `abualiexpress`, `N12chat`).
   - Requires zero Telegram API keys or phone login; parses `div.tgme_widget_message_wrap` blocks with timestamps, view counts, and full Hebrew/English text.
4. **GDELT Project 2.0 DOC API (`search_gdelt_news` / `search_gdelt`)**:
   - **Endpoint**: `GET https://api.gdeltproject.org/api/v2/doc/doc?query={query}&mode=ArtList&format=json&maxrecords={n}&timespan=7d`
   - Enforces a strict **1 request per 5 seconds** rate limit per IP across processes using `/tmp/.agent_gdelt_last_call_ts` and automatically falls back to `search_google_news_rss` if the public IP rate-limiter returns `HTTP 429`.

### 5.1 News Sources Comparison Matrix
| News Source | Access Method | Cost | Latency / Update Rate | Anti-Bot | Hebrew Support | Score |
|---|---|---|---|---|---|---|
| **Google News RSS (`IL:he` / `US:en`)** | XML RSS (`/rss/search?q=...`) | Free ($0) | Real-time (<5 min) | **None** | **Native** (`hl=he&gl=IL&ceid=IL:he`) | **9.5/10** |
| **Israeli Portal RSS (`ynet`, `mako`, `globes`…)** | Direct XML RSS (12 portals) | Free ($0) | Real-time (1–2 min) | **None** | **Native** (Explicit `utf-8` byte decode) | **9.5/10** |
| **Telegram Web Preview (`t.me/s/{channel}`)** | SSR HTML + `?q=` search | Free ($0) | Instant (<10 sec) | **None** | **Native** (Top source for breaking IL OSINT) | **9.0/10** |
| **GDELT 2.0 DOC API** | REST JSON (`ArtList` + RSS fallback) | Free ($0) | 15-min global crawl | **1 req / 5s limit** | **Good** (65+ languages indexed) | **8.5/10** |

---

## 6. Solving Browser Automation on WSL2 + Antigravity (R5)

### 6.1 Forensic Diagnosis: Why Browser Tools Failed on Your WSL Setup
Live inspection of this WSL2 environment (`Linux 6.6.87.2-microsoft-standard-WSL2`) uncovered two concrete root causes:
1. **Missing Linux Chrome Binary Inside WSL**:
   - Your WSL2 instance has **WSLg active** (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`), meaning Linux GUI/headless browsers work natively.
   - However, `/usr/bin/google-chrome` is not installed yet—even though the installer **`/mnt/c/Users/avrahamas/Downloads/google-chrome-stable_current_amd64.deb` (144 MB)** is already sitting in your Downloads folder!
2. **Windows Chrome (`v156.0.7073.0`) Cross-OS DevTools & NAT Restrictions**:
   - Launching Windows `chrome.exe` from WSL fails when MCP uses `--remote-debugging-port=9222` across the Hyper-V NAT boundary (`10.255.255.254`), AND **Chrome 136+ blocks remote debugging on the default `User Data` directory** unless an explicit non-default `--user-data-dir` is provided.

### 6.2 Three Working Solutions (Pick One)

#### Solution A (Recommended): Install the Linux Chrome `.deb` Already in Your Downloads
Run this one-time command in your WSL terminal to install Linux Chrome inside WSL2:
```bash
sudo apt-get update && sudo apt-get install -y /mnt/c/Users/avrahamas/Downloads/google-chrome-stable_current_amd64.deb
```
Once installed at `/usr/bin/google-chrome`, `chrome-devtools-mcp` works natively inside WSL2 (both headless and visible via WSLg) and can maintain a persistent logged-in profile at `$HOME/.config/chrome-agent-profile` for reading your X and Instagram accounts:
```json
"chrome-devtools": {
  "command": "npx",
  "args": [
    "-y", "chrome-devtools-mcp@latest",
    "--executablePath=/usr/bin/google-chrome",
    "--userDataDir=/home/avreymi/.config/chrome-agent-profile"
  ]
}
```

#### Solution B (Zero-Install): Windows Chrome `156.0.7073.0` with Isolated `--user-data-dir` & NAT Portproxy
If you prefer using Windows Chrome without installing Linux Chrome:
1. Launch Windows Chrome with an explicit non-default `--user-data-dir` (required by Chrome 136+):
```bash
"/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --user-data-dir='C:\temp\agy-chrome-profile'
```
2. If WSL2 is in NAT mode (default gateway `10.255.255.254`), forward port `9222` in an Administrator PowerShell window (or enable `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig`):
```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=9222 connectaddress=127.0.0.1 connectport=9222
```
3. Point `chrome-devtools-mcp` to the running browser endpoint (`--browserUrl=http://127.0.0.1:9222` in mirrored mode or `--browserUrl=http://10.255.255.254:9222` in NAT mode).

#### Solution C: Headless / Persistent Python Automation via `patchright` / `playwright`
For scripted DOM extraction without MCP, run `pip install patchright && patchright install chromium` inside WSL2 and launch a persistent context (`p.chromium.launch_persistent_context("/home/avreymi/.config/chrome-agent-profile", headless=False)`), which renders natively via WSLg XWayland.

---

## 7. Actionable User Checklist, Verification Record & Skills Integration

### 7.1 What You Need To Do (User Action Items)
1. **Fix WSL Browser (1 minute)**: Run `sudo apt-get install -y /mnt/c/Users/avrahamas/Downloads/google-chrome-stable_current_amd64.deb` in WSL so `chrome-devtools-mcp` has a native Linux Chrome binary.
2. **Connect Personal X & Instagram Accounts (2 minutes)**:
   - Either log into X.com and Instagram once inside the agent Chrome profile (`google-chrome --user-data-dir=$HOME/.config/chrome-agent-profile`), **OR**
   - Copy `auth_token` + `ct0` from `x.com` cookies (`X_AUTH_TOKEN`, `X_CT0`) and `sessionid` from `instagram.com` cookies (`IG_SESSIONID`) into `/home/avreymi/teamwork_projects/agent_data_sources/.env`.
3. **Index Your Local Book Folders**:
   - Run `python3 tools/local_books_tool.py --index "/path/to/your/books"` (already verified live on `/mnt/c/Users/avrahamas/Downloads/*.pdf` — 17 PDFs / 195 pages indexed in 12.2s).
4. **Optional Paid/Freemium API Keys (`.env`)**:
   - All core tools (`openlibrary`, `hebrewbooks`, `sefaria`, `local_books_tool`, `arctic_shift`, `rotter`, `discourse`, `stackexchange`, `news_tool` RSS/Telegram/GDELT) work **immediately with zero API keys**. Add `GOOGLE_BOOKS_API_KEY`, `SOCIALDATA_API_KEY`, `EXA_API_KEY`, or `APIFY_API_TOKEN` in `.env` only when you need commercial social/book quotas.

### 7.2 Production Tool Suite & MCP Mapping (`mcp_config.json`)
| Module | CLI Command | MCP Server Name | Key Capabilities |
|---|---|---|---|
| [`tools/book_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/book_tool.py) | `python3 tools/book_tool.py -q "ברכות" -s sefaria` | `books-search-server` | OpenLibrary, Google Books, HebrewBooks (JSONP/Suggest/Shas/PDF), Sefaria, IA OCR |
| [`tools/local_books_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/local_books_tool.py) | `python3 tools/local_books_tool.py -i /path -q "ירושלים"` | `local-books-server` | PyMuPDF + SQLite FTS5 (`unicode61` + `trigram`), nikkud strip, visual-RTL fix, prefix/sofit/gershayim expansion |
| [`tools/social_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/social_tool.py) | `python3 tools/social_tool.py -p x -m personal -q bookmarks` | `social-data-server` | SocialData (X), Exa, Apify (IG/FB), Personal X (`X_AUTH_TOKEN`/`X_CT0`) & IG (`IG_SESSIONID`) |
| [`tools/forum_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/forum_tool.py) | `python3 tools/forum_tool.py -p rotter -l 5` | `forum-search-server` | Reddit Arctic Shift (ASCII + Hebrew), Rotter (`windows-1255`), Discourse, StackExchange, SERP Dorking |
| [`tools/news_tool.py`](file:///home/avreymi/teamwork_projects/agent_data_sources/tools/news_tool.py) | `python3 tools/news_tool.py -s ynet -l 5` | `news-search-server` | Google News RSS (`IL:he`/`US:en`), 12 Israeli RSS portals, Telegram `t.me/s/`, GDELT 2.0 |

### 7.3 Live Tool Verification Summary
| Tool & Function | Live Endpoint / Target Probed | Verified Output |
|---|---|---|
| `book_tool.search_sefaria` / `fetch_sefaria_text` | `sefaria.org/api/texts/Berakhot%202a` | 2,869 chars vocalized Hebrew + English Mishnah/Gemara (`מֵאֵימָתַי קוֹרִין...`) |
| `book_tool.fetch_hebrewbooks_shas` | `hebrewbooks.org/shas.aspx?mesechta=1&daf=2` | Extracted structured Gemara (`shastext2`), Rashi (`shastext3`), and Tosafot (`shastext4`) |
| `book_tool.search_openlibrary` / `fetch_ia_text` | `openlibrary.org` (`OL1532343W` / `OL7070581M`) | Selected non-`goog` IA ID (`cu31924028576902`) and streamed OCR `_djvu.txt` |
| `local_books_tool.index_local_books` & `search_local_books` | `/mnt/c/Users/avrahamas/Downloads/*.pdf` (17 PDFs, 195 pp) | Matched `נפי` in vocalized `Hebrew_Book_of_Mormon.pdf`, `מובילאיי` in legacy-encoded PDF, and `בע"מ` / `דו"ח` across Hebrew PDFs |
| `forum_tool.search_reddit_arctic_shift` & `fetch_rotter_scoops` | `arctic-shift.photon-reddit.com`, `rotter.net/rss/rotternews.xml` | Returned `r/Israel` Hebrew posts (`ישראל`), multi-word `r/Python` posts (`asyncio exception`), and `windows-1255` decoded Rotter scoops |
| `forum_tool.search_stackexchange` | `api.stackexchange.com/2.3/search/advanced` (`judaism`) | Returned live StackExchange / Mi Yodeya Q&A threads with stripped body text |
| `news_tool.fetch_israeli_rss` & `fetch_telegram_channel` | `ynet`, `mako`, `globes` (`iID=2`), `t.me/s/amitsegal` | Verified UTF-8 Hebrew items across all 12 Israeli portals and live Telegram posts |

### 7.4 Integration with Custom Research Skills (`/home/avreymi/dotfiles/skills/`)
- **`iterative-deep-researcher`**: Import `from tools import search_books, search_local_books, search_news, search_reddit_arctic_shift, fetch_rotter_scoops` during subsurface multi-lingual discovery passes to cross-reference claims across Hebrew news portals, Telegram channels, Reddit `r/Israel`, Sefaria, and local PDFs.
- **`source-compendium-builder`**: Use `fetch_sefaria_text`, `fetch_hebrewbooks_shas`, `get_local_book_page`, and `fetch_telegram_channel` to extract verbatim primary-source Hebrew and English quotes with exact page numbers, canonical citations, and direct URLs.

### 7.5 Quick-Start Code Recipes (Python API)

```python
from tools import (
    # 1. Global & Hebrew Books
    search_books, fetch_book_text, fetch_sefaria_text, fetch_hebrewbooks_shas,
    # 2. Local Personal Book Library (PyMuPDF + SQLite FTS5)
    index_local_books, search_local_books, get_local_book_page,
    # 3. Social Networks & Personal Accounts
    search_social, fetch_personal_x_timeline, fetch_personal_instagram_feed,
    # 4. Discussion Forums
    search_reddit_arctic_shift, fetch_rotter_scoops, search_discourse_forum, search_stackexchange,
    # 5. News & Real-Time Media
    search_news, fetch_israeli_rss, fetch_telegram_channel, search_gdelt_news,
)

# --- Domain 1: Books & Judaica ---
sefaria_berakhot = fetch_sefaria_text("Berakhot 2a", max_chars=2000)
shas_daf = fetch_hebrewbooks_shas(mesechta=1, daf=2, max_chars=2000)
ol_text = fetch_book_text("OL66554W", source="openlibrary", max_chars=2000)

# --- Domain 2: Local Book Library (PDF/EPUB/TXT/MD) ---
index_local_books("/mnt/c/Users/avrahamas/Downloads")
local_hits = search_local_books('בע"מ', limit=5)          # Handles Hebrew prefixes, sofit & gershayim
page_full = get_local_book_page("Hebrew_Book_of_Mormon.pdf", page_num=14)

# --- Domain 3: Social Networks (Public & Personal Cookies) ---
x_bookmarks = search_social("bookmarks", platform="x")    # Uses X_AUTH_TOKEN & X_CT0
ig_saved = search_social("saved", platform="instagram")   # Uses IG_SESSIONID
ig_public = search_social("israel", platform="instagram", method="apify", max_results=5)

# --- Domain 4: Discussion Forums ---
reddit_il = search_reddit_arctic_shift("בינה מלאכותית", limit=5)  # Auto-routes Hebrew to r/Israel
rotter_scoops = fetch_rotter_scoops(limit=5)                      # Decodes windows-1255 bytes
mi_yodeya = search_stackexchange("Shabbat", site="judaism", limit=5)

# --- Domain 5: Breaking News & Telegram OSINT ---
ynet_breaking = fetch_israeli_rss("ynet", limit=5)
globes_he = fetch_israeli_rss("globes", limit=5)                  # Hebrew homepage feed (iID=2)
segal_tg = fetch_telegram_channel("amitsegal", query="נתניהו", limit=5)
```

### 7.6 Environment Variables Reference (`.env.example`)

| Variable | Domain / Tool | Required? | Purpose & Acquisition |
|---|---|---|---|
| `LOCAL_BOOKS_DB_PATH` | `local_books_tool.py` | Optional | Path to SQLite FTS5 index (defaults to `.local_books_index.db`) |
| `GOOGLE_BOOKS_API_KEY` | `book_tool.py` | Optional | Raises Google Books quota to 1,000 req/day (Google Cloud Console) |
| `X_AUTH_TOKEN` | `social_tool.py` | For Personal X | `auth_token` cookie from logged-in `https://x.com` DevTools |
| `X_CT0` | `social_tool.py` | For Personal X | `ct0` CSRF cookie from logged-in `https://x.com` DevTools |
| `IG_SESSIONID` | `social_tool.py` | For Personal IG | `sessionid` cookie from logged-in `https://www.instagram.com` DevTools |
| `SOCIALDATA_API_KEY` | `social_tool.py` | Optional | Public X/Twitter search at $0.20/1k tweets (`socialdata.tools`) |
| `EXA_API_KEY` | `social_tool.py` | Optional | Semantic neural search across `x.com`, `instagram.com`, `facebook.com` |
| `APIFY_API_TOKEN` | `social_tool.py` | Optional | Managed Instagram & Facebook public page scrapers (`console.apify.com`) |
| `GOOGLE_PSE_KEY` | `forum_tool.py` | Optional | Google Custom Search JSON API key for FXP/Quora SERP dorking (100/day free) |
| `GOOGLE_PSE_CX` | `forum_tool.py` | Optional | Google Programmable Search Engine ID (`cx`) for SERP dorking |
| `SERPAPI_API_KEY` | `forum_tool.py` | Optional | Alternative SERP provider for Cloudflare-protected forum dorking |

