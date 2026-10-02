from __future__ import annotations

import html
import logging
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlsplit

import feedparser
import requests

from .models import Article

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; WebAdNewsBot/1.0)"
_TAG_RE = re.compile(r"<[^>]+>")


def google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ja&gl=JP&ceid=JP:ja"


def clean_text(text: str, limit: int = 600) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _domain(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def official_tier(url: str, official_domains: dict[str, int]) -> int | None:
    """URL が公式ドメインならその tier を返す（サブドメインも一致させる）."""
    host = _domain(url)
    path = urlsplit(url).path
    best: int | None = None
    for domain, tier in official_domains.items():
        d_host, _, d_path = domain.partition("/")
        if (host == d_host or host.endswith("." + d_host)) and path.startswith("/" + d_path if d_path else ""):
            best = tier if best is None else min(best, tier)
    return best


def is_social(url: str, social_domains: list[str]) -> bool:
    host = _domain(url)
    path = urlsplit(url).path
    for domain in social_domains:
        d_host, _, d_path = domain.partition("/")
        if (host == d_host or host.endswith("." + d_host)) and path.startswith("/" + d_path if d_path else ""):
            return True
    return False


def matches_keywords(article: Article, keywords: list[str]) -> bool:
    haystack = f"{article.title} {article.summary}".lower()
    for kw in keywords:
        kw_l = kw.lower()
        # 短い英単語（ad/ads 等）は単語境界で判定して誤検出を防ぐ
        if kw_l.isascii() and len(kw_l) <= 4:
            if re.search(rf"\b{re.escape(kw_l)}\b", haystack):
                return True
        elif kw_l in haystack:
            return True
    return False


def _entry_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
    return None


def parse_feed(content: bytes, source: dict, config: dict) -> list[Article]:
    feed = feedparser.parse(content)
    official_domains = config.get("official_domains", {})
    social_domains = config.get("social_domains", [])
    articles: list[Article] = []
    for entry in feed.entries:
        url = entry.get("link")
        title = clean_text(entry.get("title", ""), 300)
        if not url or not title:
            continue

        tier = source["tier"]
        publisher = None
        # Google News は発行元を <source url="..."> で持つ
        src = entry.get("source") or {}
        publisher_url = src.get("href") if isinstance(src, dict) else None
        if source["type"] == "google_news":
            publisher = src.get("title") if isinstance(src, dict) else None
            # タイトル末尾の " - 発行元" を除去
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3]

        check_url = publisher_url or url
        upgraded = official_tier(check_url, official_domains) or official_tier(url, official_domains)
        if upgraded is not None:
            tier = min(tier, upgraded)

        articles.append(
            Article(
                title=title,
                url=url,
                source_name=source["name"],
                tier=tier,
                summary=clean_text(entry.get("summary", "")),
                published=_entry_published(entry),
                platform=source.get("platform"),
                priority=source.get("priority", "medium"),
                publisher=publisher,
                is_social=is_social(check_url, social_domains),
            )
        )
    return articles


def fetch_source(source: dict, config: dict, session: requests.Session, timeout: int = 20) -> list[Article]:
    url = google_news_url(source["query"]) if source["type"] == "google_news" else source["url"]
    resp = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return parse_feed(resp.content, source, config)


def collect(config: dict, since: datetime, session: requests.Session | None = None) -> list[Article]:
    """全ソースから since 以降の記事を収集する。取得に失敗したソースはスキップする."""
    session = session or requests.Session()
    keywords = config.get("keywords", [])
    collected: dict[str, Article] = {}
    for source in config["sources"]:
        try:
            articles = fetch_source(source, config, session)
        except Exception as exc:  # noqa: BLE001 - 1 ソースの失敗で全体を止めない
            log.warning("ソース取得失敗: %s (%s)", source["name"], exc)
            continue

        kept = 0
        for article in articles:
            if article.published and article.published < since:
                continue
            if article.published is None and source["type"] == "google_news":
                continue  # 日付不明の検索結果は古い可能性が高い
            if source.get("keyword_filter") and not matches_keywords(article, keywords):
                continue
            existing = collected.get(article.id)
            # 同じ URL が複数ソースに出た場合は、より一次情報に近い tier を採用
            if existing is None or article.tier < existing.tier:
                collected[article.id] = article
                kept += 1
        log.info("%s: %d 件取得 / %d 件対象", source["name"], len(articles), kept)
    # 一次情報に近い順、同じ tier 内では新しい順
    return sorted(collected.values(), key=lambda a: (a.tier, -(a.published or since).timestamp()))


def default_since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)
