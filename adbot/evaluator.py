from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import anthropic

from .models import Article, Evaluation

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"

CATEGORIES = [
    "媒体アップデート",
    "新広告メニュー・フォーマット",
    "新配信手法",
    "ターゲティング変更",
    "計測・コンバージョン変更",
    "新広告媒体",
    "AI×広告運用",
    "クリエイティブトレンド",
    "業界トレンド",
    "その他",
]

SYSTEM_PROMPT = """\
あなたは日本の WEB 広告運用チームのリサーチ担当です。
収集されたニュース・公式発表の一覧を読み、「広告運用担当者が今知っておくべき重要な情報か」を厳しく判定します。
このチームは毎日ニュースを検索する時間がなく、あなたの判定で Slack に通知されたものだけを読みます。
通知が多すぎると誰も読まなくなるため、迷ったら通知しないでください。重要な情報が 1 件もない日があって当然です。

## 通知すべき情報（notify=true の候補）
- 広告配信の仕様が大きく変わる
- 新しい広告メニュー・広告フォーマットが開始される
- 新しい配信方法が登場する
- ターゲティング仕様が変わる
- 計測方法・コンバージョン仕様が変わる（CAPI、Cookie、プライバシー規制、アトリビューション等）
- P-MAX、Demand Gen、AI Max、Meta Advantage+ など主要機能に大きな変更がある
- 新しい広告媒体が日本で利用可能になる
- 今後の広告運用で実際にテストする価値がありそう
- WEB 広告業界で明確なトレンド変化がある（AI × 広告運用の重要な動きを含む）

## 通知しない情報（notify=false）
- 単なる企業ニュース（決算、人事、提携発表のみ、受賞など）
- 広告運用に関係の薄いニュース
- 小さな仕様変更（UI の軽微な変更、レポート列の名称変更など）
- 海外だけの情報で、日本の広告運用への影響がほぼないもの
- 「これまでに通知済みのトピック」と同じ内容（続報で運用上の新事実が加わった場合のみ通知可）
- SNS 上の未確認情報、噂、リーク
- 他記事の焼き直し・まとめ記事・ハウツー記事で新事実がないもの
- セミナー・ウェビナー告知、自社サービスの宣伝記事

## 情報源の信頼度（tier）
1: 媒体の公式発表 / 2: 公式ブログ・ヘルプ / 3: 公式ドキュメント / 4: 国内マーケティングメディア / 5: 海外メディア
一次情報（tier 1-3）を重視してください。tier 4-5 のみが根拠の場合は、内容が具体的で信頼できるときに限り通知します。

## importance（1-5）
5: 運用方針・予算配分に直結し、全員がすぐ知るべき（例: 主要機能の大幅変更、計測仕様の破壊的変更）
4: 運用上の対応やテスト検討が必要
3: 知っておくと良いが急ぎではない
2: 参考程度
1: 運用に無関係
notify=true にするのは importance 4 以上の情報のみです。

## 出力
- 入力の全記事について、id をそのまま使って 1 件ずつ評価を返してください。
- title_ja / summary_ja / impact / action は日本語で、広告運用担当者が読んで即理解できるよう簡潔かつ具体的に書いてください。
  - summary_ja: 何が変わるのか・いつからか・対象範囲（2-3 文）
  - impact: 広告運用への影響（1-2 文）
  - action: 運用担当者が取るべき具体的アクション（1-2 文。不要なら「特になし」）
- topic_key: 同じ出来事を指す記事が同じキーになるよう、"媒体-機能-変更内容" の形式の英小文字ケバブケースで付けてください
  （例: "google-ads-pmax-channel-reporting", "meta-advantage-plus-shopping-merge"）。
  これまでに通知済みのトピックと同一の出来事であれば、そのトピックの topic_key をそのまま使ってください。
- japan_relevance: 日本の広告運用への関連度（high / medium / low）
- reason: 判定理由を 1 文で。
"""

_EVAL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "notify": {"type": "boolean"},
        "importance": {"type": "integer"},
        "category": {"type": "string", "enum": CATEGORIES},
        "platform": {"type": "string"},
        "title_ja": {"type": "string"},
        "summary_ja": {"type": "string"},
        "impact": {"type": "string"},
        "action": {"type": "string"},
        "topic_key": {"type": "string"},
        "japan_relevance": {"type": "string", "enum": ["high", "medium", "low"]},
        "duplicate_of_notified": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": [
        "id",
        "notify",
        "importance",
        "category",
        "platform",
        "title_ja",
        "summary_ja",
        "impact",
        "action",
        "topic_key",
        "japan_relevance",
        "duplicate_of_notified",
        "reason",
    ],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"evaluations": {"type": "array", "items": _EVAL_ITEM_SCHEMA}},
    "required": ["evaluations"],
    "additionalProperties": False,
}


class EvaluationError(RuntimeError):
    pass


def _article_payload(article: Article) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "summary": article.summary,
        "url": article.url,
        "source": article.source_name,
        "publisher": article.publisher,
        "tier": article.tier,
        "platform_hint": article.platform,
        "watch_priority": article.priority,
        "published": article.published.isoformat() if article.published else None,
    }


def build_user_message(articles: list[Article], notified: list[dict]) -> str:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    notified_lines = [{"topic_key": n["topic_key"], "title": n["title"]} for n in notified]
    return (
        f"今日の日付: {today}\n\n"
        "## これまでに通知済みのトピック（直近）\n"
        f"{json.dumps(notified_lines, ensure_ascii=False, indent=1) if notified_lines else 'なし'}\n\n"
        f"## 評価対象の記事（{len(articles)} 件）\n"
        f"{json.dumps([_article_payload(a) for a in articles], ensure_ascii=False, indent=1)}"
    )


def parse_evaluations(data: dict, valid_ids: set[str]) -> list[Evaluation]:
    results: list[Evaluation] = []
    for item in data.get("evaluations", []):
        if item.get("id") not in valid_ids:
            log.warning("未知の記事 ID を無視: %s", item.get("id"))
            continue
        importance = max(1, min(5, int(item["importance"])))
        results.append(
            Evaluation(
                article_id=item["id"],
                # 通知済みトピックの重複と判定されたものは通知しない
                notify=bool(item["notify"]) and not item.get("duplicate_of_notified", False),
                importance=importance,
                category=item["category"],
                platform=item["platform"],
                title_ja=item["title_ja"],
                summary_ja=item["summary_ja"],
                impact=item["impact"],
                action=item["action"],
                topic_key=item["topic_key"].strip().lower(),
                japan_relevance=item["japan_relevance"],
                reason=item["reason"],
            )
        )
    return results


class Evaluator:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        batch_size: int = 25,
        effort: str = "high",
    ):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.batch_size = batch_size
        self.effort = effort

    def _evaluate_batch(self, articles: list[Article], notified: list[dict]) -> list[Evaluation]:
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(articles, notified)}],
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "refusal":
            raise EvaluationError(f"モデルが評価を拒否しました: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise EvaluationError("出力が max_tokens に達しました。batch_size を小さくしてください")

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"評価結果の JSON を解析できません: {exc}") from exc
        return parse_evaluations(data, {a.id for a in articles})

    def evaluate(self, articles: list[Article], notified: list[dict]) -> tuple[list[Evaluation], set[str]]:
        """記事を評価する。戻り値は (評価結果, 評価に成功した記事 ID)."""
        evaluations: list[Evaluation] = []
        evaluated_ids: set[str] = set()
        for start in range(0, len(articles), self.batch_size):
            batch = articles[start : start + self.batch_size]
            try:
                results = self._evaluate_batch(batch, notified)
            except (EvaluationError, anthropic.APIError) as exc:
                # 失敗したバッチは未評価のまま残し、次回実行で再評価する
                log.error("評価失敗（%d 件、次回再評価）: %s", len(batch), exc)
                continue
            evaluations.extend(results)
            evaluated_ids.update(r.article_id for r in results)
            log.info("評価完了: %d/%d 件", start + len(batch), len(articles))
        return evaluations, evaluated_ids
