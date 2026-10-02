from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class StateStore:
    """評価済み記事と通知済みトピックを JSON ファイルで保持する.

    - seen:     評価済み記事 ID -> 評価日時（同じ記事を何度も AI 評価しない）
    - notified: 通知済みトピック（AI による重複判定と topic_key 一致による除外に使う）
    """

    def __init__(self, path: str | Path, seen_days: int = 30, notified_days: int = 90):
        self.path = Path(path)
        self.seen_days = seen_days
        self.notified_days = notified_days
        self.seen: dict[str, str] = {}
        self.notified: list[dict] = []
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.seen = data.get("seen", {})
            self.notified = data.get("notified", [])

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def is_seen(self, article_id: str) -> bool:
        return article_id in self.seen

    def mark_seen(self, article_ids) -> None:
        now = self._now().isoformat()
        for article_id in article_ids:
            self.seen[article_id] = now

    def notified_topic_keys(self) -> set[str]:
        return {n["topic_key"] for n in self.notified}

    def recent_notified(self, days: int = 60) -> list[dict]:
        cutoff = self._now() - timedelta(days=days)
        return [n for n in self.notified if datetime.fromisoformat(n["notified_at"]) >= cutoff]

    def add_notified(self, topic_key: str, title: str, url: str) -> None:
        self.notified.append(
            {"topic_key": topic_key, "title": title, "url": url, "notified_at": self._now().isoformat()}
        )

    def prune(self) -> None:
        now = self._now()
        seen_cutoff = now - timedelta(days=self.seen_days)
        self.seen = {k: v for k, v in self.seen.items() if datetime.fromisoformat(v) >= seen_cutoff}
        notified_cutoff = now - timedelta(days=self.notified_days)
        self.notified = [n for n in self.notified if datetime.fromisoformat(n["notified_at"]) >= notified_cutoff]

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"seen": self.seen, "notified": self.notified}, ensure_ascii=False, indent=1)
        # 書き込み途中で落ちても状態ファイルが壊れないよう一時ファイル経由で置き換える
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
        os.replace(tmp, self.path)
