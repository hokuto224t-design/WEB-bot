from __future__ import annotations

from .models import Article, Evaluation, Notification


def select_notifications(
    articles: list[Article],
    evaluations: list[Evaluation],
    notified_topic_keys: set[str],
    min_importance: int = 4,
    max_items: int = 10,
) -> list[Notification]:
    """AI の評価結果から通知対象を選び、同一トピックをまとめる.

    ルールベースの安全弁:
    - importance が閾値未満は通知しない
    - SNS 由来の記事だけを根拠にしたトピックは通知しない
    - 海外メディア（tier 5）のみが根拠で日本との関連度が low のものは通知しない
    - 過去に通知した topic_key は通知しない
    """
    by_id = {a.id: a for a in articles}
    groups: dict[str, list[tuple[Evaluation, Article]]] = {}
    for ev in evaluations:
        article = by_id.get(ev.article_id)
        if article is None or not ev.notify or ev.importance < min_importance:
            continue
        if ev.topic_key in notified_topic_keys:
            continue
        groups.setdefault(ev.topic_key, []).append((ev, article))

    notifications: list[Notification] = []
    for items in groups.values():
        trusted = [(ev, a) for ev, a in items if not a.is_social]
        if not trusted:
            continue
        # 一次情報に近いもの → 重要度が高いもの を代表記事にする
        trusted.sort(key=lambda x: (x[1].tier, -x[0].importance))
        ev, primary = trusted[0]
        if primary.tier >= 5 and ev.japan_relevance == "low":
            continue
        importance = max(e.importance for e, _ in trusted)
        ev.importance = importance
        related = [a for _, a in trusted[1:]]
        notifications.append(Notification(evaluation=ev, primary=primary, related=related))

    notifications.sort(key=lambda n: (-n.evaluation.importance, n.primary.tier))
    return notifications[:max_items]
