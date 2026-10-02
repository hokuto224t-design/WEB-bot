from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from . import slack
from .collector import collect, default_since
from .evaluator import DEFAULT_MODEL, Evaluator
from .selection import select_notifications
from .store import StateStore

log = logging.getLogger("adbot")

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WEB広告情報収集 Slack Bot")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    p.add_argument("--state", type=Path, default=ROOT / "data" / "state.json")
    p.add_argument("--since-hours", type=int, default=72, help="この時間以内に公開された記事を対象にする")
    p.add_argument("--min-importance", type=int, default=4, help="通知する最低重要度（1-5）")
    p.add_argument("--max-items", type=int, default=10, help="1 回の通知の最大件数")
    p.add_argument("--max-articles", type=int, default=150, help="1 回に AI 評価する最大記事数")
    p.add_argument("--model", default=os.environ.get("ADBOT_MODEL", DEFAULT_MODEL))
    p.add_argument("--dry-run", action="store_true", help="Slack に送らず結果を表示する（状態も保存しない）")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = StateStore(args.state)

    articles = collect(config, since=default_since(args.since_hours))
    new_articles = [a for a in articles if not store.is_seen(a.id)][: args.max_articles]
    log.info("収集 %d 件 / 未評価 %d 件", len(articles), len(new_articles))
    if not new_articles:
        log.info("新しい記事なし。通知しません")
        if not args.dry_run:
            store.save()
        return 0

    evaluator = Evaluator(model=args.model)
    evaluations, evaluated_ids = evaluator.evaluate(new_articles, store.recent_notified())
    for ev in evaluations:
        log.info("[%s imp=%d] %s - %s", "通知" if ev.notify else "除外", ev.importance, ev.title_ja, ev.reason)

    notifications = select_notifications(
        new_articles,
        evaluations,
        store.notified_topic_keys(),
        min_importance=args.min_importance,
        max_items=args.max_items,
    )

    if args.dry_run:
        print(f"通知対象: {len(notifications)} 件")
        for n in notifications:
            print(f"- [{n.evaluation.importance}] {n.evaluation.platform}: {n.evaluation.title_ja} {n.primary.url}")
        return 0

    if notifications:
        webhook = os.environ.get("SLACK_WEBHOOK_URL")
        if not webhook:
            log.error("SLACK_WEBHOOK_URL が未設定です")
            return 1
        slack.post(webhook, notifications)
        for n in notifications:
            store.add_notified(n.evaluation.topic_key, n.evaluation.title_ja, n.primary.url)
        log.info("Slack に %d 件通知しました", len(notifications))
    else:
        log.info("重要な情報はありませんでした。Slack には送信しません")

    store.mark_seen(evaluated_ids)
    store.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
