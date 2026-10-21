# Original User Request

## Initial Request — 2026-10-20T22:20:29Z

Conduct deep research and deliver a comprehensive architectural blueprint and implementation guide for expanding AI agent information sources across three domains: Books, Social Networks (X/Twitter, Facebook, Instagram), and Discussion Forums. The report must provide concrete access methods (APIs, scrapers, MCP servers, search filters), comparative trade-offs, and ready-to-use configurations for both international and Hebrew sources.

Working directory: ~/teamwork_projects/agent_data_sources
Integrity mode: development

## Requirements

### R1. Books Search & Retrieval Architecture
Examine and document full-text and metadata search capabilities across global and Hebrew book repositories.
- Cover Google Books API, Internet Archive / OpenLibrary, Project Gutenberg, and Semantic Scholar.
- Include dedicated coverage of Hebrew book repositories: HebrewBooks.org, Ben-Yehuda Project, and National Library of Israel open data.
- Detail access methods (REST APIs, open dumps, full-text extraction), license/copyright boundaries, and ready MCP servers or Python tool implementations.

### R2. Social Networks Access (X/Twitter, Facebook, Instagram)
Examine and document viable retrieval mechanisms for posts, threads, media, and search queries across X.com, Facebook, and Instagram.
- Compare official APIs (capabilities, strictness, enterprise tier costs) vs. third-party aggregators/APIs (Apify actors, Exa.ai, SocialData, RapidAPI) vs. open-source scrapers and headless tools.
- Detail practical anti-bot challenges (Cloudflare, login walls, rate limits, session/cookie handling) and concrete strategies for agents to navigate them safely.
- Provide trade-off analysis between cost, reliability, maintenance overhead, and latency.

### R3. Community Forums & Discussion Boards
Examine and document structured search and extraction across global forums (Reddit via official API, Arctic Shift, Pullpush; Quora; StackExchange; Discourse platforms) and prominent Hebrew forums (Rotter.net, FXP, Tapuz).
- Document specialized forum search strategies (Google SERP / Dorking operators, Exa domain filtering, RSS/Atom feeds, site-specific search endpoints).
- Evaluate existing MCP servers and tools for forum exploration.

### R4. Tool Integration Blueprint & Configuration Guide
Provide an actionable integration blueprint for agents (specifically Antigravity / Gemini / MCP-compatible agents).
- Categorize solutions into Free/Self-hosted (open source scrapers, public APIs) vs. Commercial/Managed (Exa, Apify, SerpApi).
- Supply concrete configuration files, MCP server declarations, or Python code templates demonstrating how an agent invokes searches in each domain.

## Acceptance Criteria

### Coverage & Completeness
- [ ] Compendium covers all three required domains: Books (Global + Hebrew), Social Networks (X, Facebook, Instagram), and Forums (Global + Hebrew).
- [ ] Each domain contains an explicit comparison matrix with: Access Method, Cost Tier, Rate Limits, Anti-Bot Severity, Hebrew Support, and Reliability Score.

### Technical Depth & Feasibility
- [ ] Real-world feasibility analysis detailing current (2025/2026) login wall and anti-bot policies for X, Facebook, and Instagram, with verified workarounds or recommended managed APIs.
- [ ] Clear distinction between metadata search (title, author, tags) and full-text content extraction for books and forum threads.

### Actionable Deliverables
- [ ] Working code examples or MCP tool configurations provided for at least:
  - 1 Book search tool (e.g. Google Books API / OpenLibrary / HebrewBooks)
  - 1 Social Media search tool (e.g. Apify actor / Exa / twikit / X API)
  - 1 Forum search tool (e.g. Reddit Arctic Shift / custom SERP dork / Discourse API)
- [ ] Comprehensive Markdown guide written to `~/teamwork_projects/agent_data_sources/AGENT_DATA_SOURCES_GUIDE.md` along with reference configuration files in the working directory.
