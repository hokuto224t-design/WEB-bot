from __future__ import annotations

from datetime import datetime, timezone

from .models import Article, Evaluation, Notification
from .slack import JST

_TIER_LABEL = {1: "公式発表", 2: "公式ブログ/ヘルプ", 3: "公式ドキュメント", 4: "国内メディア", 5: "海外メディア"}


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _date(article: Article) -> str:
    return f"{article.published.astimezone(JST):%Y-%m-%d}" if article.published else "-"


def _failed_sources_section(failed_sources: list[str]) -> list[str]:
    if not failed_sources:
        return []
    return [f"## 取得に失敗した情報源（{len(failed_sources)} 件）", "", *[f"- {name}" for name in failed_sources], ""]


def collection_report(
    articles: list[Article], failed_sources: list[str] | None = None, now: datetime | None = None
) -> str:
    """AI 評価前の収集結果（--collect-only 用）."""
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    lines = [f"# 収集結果（{now:%Y/%m/%d %H:%M} JST）", "", f"対象記事: {len(articles)} 件", ""]
    lines += _failed_sources_section(failed_sources or [])
    lines += ["| tier | 公開日 | 情報源 | タイトル |", "|---|---|---|---|"]
    for a in articles:
        source = a.publisher or a.source_name
        lines.append(f"| {a.tier} | {_date(a)} | {_md(source)} | [{_md(a.title)}]({a.url}) |")
    return "\n".join(lines) + "\n"


def dry_run_report(
    articles: list[Article],
    evaluations: list[Evaluation],
    notifications: list[Notification],
    failed_count: int = 0,
    failed_sources: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Slack に送る予定の内容と、除外した記事の判定理由をまとめた Markdown."""
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    by_id = {a.id: a for a in articles}
    notified_ids = {n.primary.id for n in notifications} | {r.id for n in notifications for r in n.related}

    lines = [
        f"# WEB広告 重要アップデート dry-run（{now:%Y/%m/%d %H:%M} JST）",
        "",
        f"- 評価対象: {len(articles)} 件 / 評価成功: {len(evaluations)} 件"
        + (f" / 評価失敗: {failed_count} 件（次回再評価）" if failed_count else ""),
        f"- **通知予定: {len(notifications)} 件**",
        "",
    ]
    lines += _failed_sources_section(failed_sources or [])
    lines += ["## 通知予定", ""]
    if not notifications:
        lines += ["重要な情報はありませんでした（Slack には何も送信されません）。", ""]
    for i, n in enumerate(notifications, 1):
        ev, a = n.evaluation, n.primary
        lines += [
            f"### {i}. [{ev.importance}] {ev.title_ja}",
            "",
            f"- 媒体 / カテゴリ: {ev.platform} / {ev.category}",
            f"- 出典: [{a.publisher or a.source_name}]({a.url})（{_TIER_LABEL.get(a.tier, '')}・{_date(a)}）",
            f"- 概要: {ev.summary_ja}",
            f"- 運用への影響: {ev.impact}",
            f"- 推奨アクション: {ev.action}",
            f"- 判定理由: {ev.reason}",
            f"- topic_key: `{ev.topic_key}`",
        ]
        if n.related:
            refs = " / ".join(f"[{r.publisher or r.source_name}]({r.url})" for r in n.related)
            lines.append(f"- 関連記事: {refs}")
        lines.append("")

    excluded = sorted(
        (ev for ev in evaluations if ev.article_id not in notified_ids),
        key=lambda ev: -ev.importance,
    )
    lines += [f"## 除外した記事（{len(excluded)} 件）", ""]
    if excluded:
        lines += ["| 重要度 | AI判定 | 媒体 | タイトル | 理由 |", "|---|---|---|---|---|"]
        for ev in excluded:
            a = by_id[ev.article_id]
            verdict = "通知候補" if ev.notify else "除外"
            lines.append(
                f"| {ev.importance} | {verdict} | {_md(ev.platform)} | [{_md(a.title)}]({a.url}) | {_md(ev.reason)} |"
            )
        lines += ["", "※「AI判定: 通知候補」なのに除外されたものは、重要度の閾値・SNS 由来・海外限定・通知済みトピック・件数上限のいずれかで除外されています。"]
    return "\n".join(lines) + "\n"
