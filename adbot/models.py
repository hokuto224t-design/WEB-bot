from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "oc", "spm"}


def normalize_url(url: str) -> str:
    """トラッキングパラメータ等を除去した比較用 URL."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def url_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


@dataclass
class Article:
    title: str
    url: str
    source_name: str
    tier: int
    summary: str = ""
    published: datetime | None = None
    platform: str | None = None
    priority: str = "medium"
    publisher: str | None = None  # Google News 経由時の実際の発行元
    is_social: bool = False

    @property
    def id(self) -> str:
        return url_id(self.url)


@dataclass
class Evaluation:
    article_id: str
    notify: bool
    importance: int
    category: str
    platform: str
    title_ja: str
    summary_ja: str
    impact: str
    action: str
    topic_key: str
    japan_relevance: str
    reason: str


@dataclass
class Notification:
    """Slack に送る 1 トピック（同一トピックの複数記事をまとめたもの）."""

    evaluation: Evaluation
    primary: Article
    related: list[Article] = field(default_factory=list)
