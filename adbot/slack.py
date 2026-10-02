from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .models import Notification

JST = timezone(timedelta(hours=9))

_IMPORTANCE_LABEL = {5: ":rotating_light: 最重要", 4: ":warning: 重要"}
_TIER_LABEL = {1: "公式発表", 2: "公式ブログ/ヘルプ", 3: "公式ドキュメント", 4: "国内メディア", 5: "海外メディア"}

# Slack の Block Kit 制限（1 メッセージ 50 ブロック、section テキスト 3000 文字）
_MAX_BLOCKS = 50
_MAX_TEXT = 2900


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _notification_blocks(n: Notification) -> list[dict]:
    ev = n.evaluation
    a = n.primary
    label = _IMPORTANCE_LABEL.get(ev.importance, "重要")
    source = a.publisher or a.source_name
    lines = [
        f"*{label}｜{_escape(ev.platform)}｜{_escape(ev.category)}*",
        f"*<{a.url}|{_escape(ev.title_ja)}>*",
        "",
        f"*概要:* {_escape(ev.summary_ja)}",
        f"*運用への影響:* {_escape(ev.impact)}",
        f"*推奨アクション:* {_escape(ev.action)}",
    ]
    context = f"出典: {_escape(source)}（{_TIER_LABEL.get(a.tier, '')}）"
    if a.published:
        context += f"｜{a.published.astimezone(JST):%Y-%m-%d}"
    if n.related:
        refs = " / ".join(f"<{r.url}|{_escape(r.publisher or r.source_name)}>" for r in n.related[:3])
        context += f"｜関連: {refs}"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _truncate("\n".join(lines))}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _truncate(context)}]},
        {"type": "divider"},
    ]


def build_messages(notifications: list[Notification], now: datetime | None = None) -> list[dict]:
    """Slack に送るペイロードのリストを作る（ブロック数上限を超える場合は分割）."""
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    header_text = f"WEB広告 重要アップデート（{now:%Y/%m/%d}）{len(notifications)}件"
    header = [{"type": "header", "text": {"type": "plain_text", "text": header_text}}]

    messages: list[dict] = []
    blocks = list(header)
    for n in notifications:
        nb = _notification_blocks(n)
        if len(blocks) + len(nb) > _MAX_BLOCKS:
            messages.append({"text": header_text, "blocks": blocks})
            blocks = []
        blocks.extend(nb)
    if blocks:
        messages.append({"text": header_text, "blocks": blocks})
    return messages


def post(webhook_url: str, notifications: list[Notification], timeout: int = 20) -> None:
    for payload in build_messages(notifications):
        resp = requests.post(webhook_url, json=payload, timeout=timeout)
        resp.raise_for_status()
