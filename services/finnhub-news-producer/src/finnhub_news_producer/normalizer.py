"""
Maps a raw Finnhub company-news dict → NewsArticle.

Finnhub /company-news field mapping:
  Finnhub key  │  Meaning                │  NewsArticle field
  ─────────────┼─────────────────────────┼────────────────────
  id           │  article numeric id     │  (ignored, use MD5)
  headline     │  article title          │  headline
  summary      │  short summary          │  summary
  url          │  article URL            │  url (→ article_id)
  source       │  source name            │  source
  category     │  news category          │  category
  datetime     │  Unix timestamp (sec)   │  published_at_ms (×1000)

Reference: https://finnhub.io/docs/api/company-news
"""

from finnhub_news_producer.schema import NewsArticle


def normalize(raw: dict, symbol: str) -> NewsArticle:
    """
    Convert one Finnhub raw article dict to a validated NewsArticle.

    Raises KeyError if url or headline is missing.
    Raises pydantic.ValidationError if field values are invalid.
    """
    url: str = raw["url"]
    return NewsArticle(
        article_id=NewsArticle.make_article_id(url),
        symbol=symbol,
        headline=raw["headline"],
        summary=raw.get("summary") or None,
        url=url,
        source=raw.get("source", "unknown"),
        category=raw.get("category") or None,
        published_at_ms=int(raw["datetime"]) * 1000,
        fetched_at_ms=NewsArticle.now_ms(),
    )
