"""
Agent Data Sources - Production Tool Suite for Autonomous AI Agents.
Provides high-performance clients for Global & Hebrew Books, Local Book Libraries,
Social Networks (Public & Personal Accounts), Discussion Forums, and Real-Time News.
"""

from .book_tool import (
    search_books,
    search_openlibrary,
    search_google_books,
    search_hebrewbooks,
    suggest_hebrewbooks,
    fetch_hebrewbooks_shas,
    download_hebrewbooks_pdf,
    search_sefaria,
    fetch_sefaria_text,
    fetch_ia_text,
    fetch_book_text,
    BookItem,
)

from .local_books_tool import (
    strip_nikkud,
    fix_legacy_hebrew_pdf,
    expand_hebrew_word_fts5,
    build_fts5_query,
    index_local_books,
    search_local_books,
    get_local_book_page,
)

from .social_tool import (
    search_social,
    search_socialdata_tweets,
    search_exa_social,
    fetch_apify_instagram,
    fetch_apify_facebook,
    fetch_personal_x_timeline,
    fetch_personal_instagram_feed,
    SocialPost,
)

from .forum_tool import (
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

from .news_tool import (
    search_news,
    search_google_news_rss,
    fetch_israeli_rss,
    fetch_telegram_channel,
    search_gdelt_news,
    search_gdelt,
    ISRAELI_RSS_FEEDS,
    NewsItem,
)

__all__ = [
    # Books
    "search_books",
    "search_openlibrary",
    "search_google_books",
    "search_hebrewbooks",
    "suggest_hebrewbooks",
    "fetch_hebrewbooks_shas",
    "download_hebrewbooks_pdf",
    "search_sefaria",
    "fetch_sefaria_text",
    "fetch_ia_text",
    "fetch_book_text",
    "BookItem",
    # Local Books
    "strip_nikkud",
    "fix_legacy_hebrew_pdf",
    "expand_hebrew_word_fts5",
    "build_fts5_query",
    "index_local_books",
    "search_local_books",
    "get_local_book_page",
    # Social
    "search_social",
    "search_socialdata_tweets",
    "search_exa_social",
    "fetch_apify_instagram",
    "fetch_apify_facebook",
    "fetch_personal_x_timeline",
    "fetch_personal_instagram_feed",
    "SocialPost",
    # Forums
    "search_reddit_arctic_shift",
    "search_arctic_shift",
    "fetch_reddit_arctic_shift",
    "fetch_rotter_scoops",
    "build_serp_forum_dork",
    "search_discourse_forum",
    "search_stackexchange",
    "fetch_stackexchange",
    "search_forum_serp_dork",
    "ForumPost",
    "RotterScoop",
    # News
    "search_news",
    "search_google_news_rss",
    "fetch_israeli_rss",
    "fetch_telegram_channel",
    "search_gdelt_news",
    "search_gdelt",
    "ISRAELI_RSS_FEEDS",
    "NewsItem",
]

__version__ = "2.0.0"
