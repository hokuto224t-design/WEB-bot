# WEB-bot

WEB広告・デジタルマーケティングの最新情報を収集し、**重要な情報があるときだけ** Slack に通知する Bot。

- 広告媒体の公式発表・公式ブログ・国内外のマーケティングメディアを RSS で毎朝収集
- Claude（`claude-opus-5-5`）が「広告運用担当者が知るべき情報か」を判定
- 重要度 4 以上（5段階）のものだけを Slack に通知。**該当がない日は何も送信しない**（※Slack 連携は現在保留中。dry-run で判定結果を確認できます）
- 一度評価・通知した内容は記録し、同じ記事・同じトピックを二度通知しない

## 仕組み

```
config/sources.yaml の情報源
        │  RSS / Google News RSS 検索で収集（直近72時間、評価済み記事は除外）
        ▼
adbot/collector.py ── 一般メディアは広告関連キーワードで事前フィルタ
        │            公式ドメインの記事は tier（信頼度）を引き上げ
        ▼
adbot/evaluator.py ── Claude が記事ごとに判定（構造化出力 JSON）
        │            notify / importance / カテゴリ / 日本語要約 / 運用への影響 / 推奨アクション / topic_key
        ▼
adbot/selection.py ── ルールベースの安全弁
        │            ・importance < 4 は除外
        │            ・SNS 由来のみの情報は除外
        │            ・海外メディアのみ & 日本との関連度 low は除外
        │            ・通知済み topic_key は除外、同一トピックは 1 件にまとめ一次情報を代表に
        ▼
adbot/slack.py ───── 通知対象があるときだけ Slack Incoming Webhook に投稿
        │
        ▼
data/state.json ──── 評価済み記事（30日）・通知済みトピック（90日）を保存
```

### 情報源の優先順位（tier）

| tier | 種別 | 例 |
|---|---|---|
| 1 | 各広告媒体の公式発表 | Google Ads & Commerce Blog, Meta Newsroom, LINEヤフー for Business, TikTok Newsroom |
| 2 | 公式ブログ・ヘルプ | Google 広告ヘルプ, Meta for Business |
| 3 | 公式ドキュメント | Google Ads Developer Blog, Meta for Developers |
| 4 | 国内マーケティングメディア | MarkeZine, Web担当者Forum, DIGIDAY[日本版] |
| 5 | 海外メディア | Search Engine Land, Social Media Today, PPC Land |

RSS を提供していない媒体（Yahoo!広告 / LINE広告 / X Ads / SmartNews Ads 等）は Google News の RSS 検索で補完しています。
Google News 経由でも、発行元が公式ドメイン（`official_domains`）であれば tier が引き上げられます。

### Slack 通知の例

```
WEB広告 重要アップデート（2026/10/03）2件
────────────────────────
⚠ 重要｜Google Ads｜媒体アップデート
P-MAX でチャネル別レポートが全アカウントに提供開始
概要: …
運用への影響: …
推奨アクション: …
出典: Google Ads & Commerce Blog（公式発表）｜2026-10-02｜関連: Search Engine Land
```

## 現在の状態

**Slack 連携は保留中です。** いまは dry-run（Slack に送らず、判定結果をレポートで確認する）までを実装しています。
Slack 送信のコード（`adbot/slack.py`）は残してあり、再開時はワークフローを戻すだけで使えます。

## dry-run の実行

### GitHub Actions で実行（推奨）

1. リポジトリの **Settings → Secrets and variables → Actions** に `ANTHROPIC_API_KEY` を登録
2. **Actions → Dry run → Run workflow** を実行
   - `mode`: `dry-run`（AI 評価まで）または `collect-only`（収集のみ・API キー不要）
   - `since_hours`: 対象期間（既定 72 時間）
3. 実行画面の **Summary** にレポートが表示されます（Artifact `report` からも `report.md` をダウンロード可）

まず `collect-only` で各情報源が取得できているか確認し、その後 `dry-run` で AI の判定を確認する流れがおすすめです。

### ローカルで実行

```bash
pip install -r requirements.txt

python -m adbot.main --collect-only                    # 収集結果のみ（API キー不要）

export ANTHROPIC_API_KEY=sk-ant-...
python -m adbot.main --dry-run --report out/report.md  # AI 評価まで実行してレポート出力
```

dry-run は状態ファイル（`data/state.json`）を更新しないため、何度実行しても同じ記事が評価対象になります。

### レポートの内容

- **通知予定**: Slack に送られる予定の記事（重要度・日本語要約・運用への影響・推奨アクション・判定理由）
- **除外した記事**: AI が除外した記事と、その重要度・理由（判定基準の調整用）
- **取得に失敗した情報源**: フィード URL の変更などで取得できなかったソース

終了コードは、すべての情報源の取得に失敗した場合と、AI 評価がすべて失敗した場合に 1 になります（Actions 上で失敗として表示）。

主なオプション:

| オプション | 既定値 | 説明 |
|---|---|---|
| `--dry-run` | - | Slack に送らず判定結果をレポート出力（状態も保存しない） |
| `--collect-only` | - | 収集結果のみ出力（AI 評価なし） |
| `--report` | - | レポート（Markdown）の出力先ファイル。未指定時は標準出力のみ |
| `--since-hours` | 72 | この時間以内に公開された記事を対象にする |
| `--min-importance` | 4 | 通知する最低重要度（1-5）。通知を減らしたいときは 5 |
| `--max-items` | 10 | 1回の通知の最大件数 |
| `--max-articles` | 150 | 1回に AI 評価する最大記事数（超過分は次回評価） |
| `--model` | `claude-opus-5-5` | 使用モデル（環境変数 `ADBOT_MODEL` でも指定可） |

## Slack 連携の再開手順（保留中）

1. Slack App で Incoming Webhook を発行し、Secrets に `SLACK_WEBHOOK_URL` を登録
2. 毎日実行のワークフロー（schedule で `python -m adbot.main` を実行し、`data/state.json` をコミットするもの）を追加
   - 状態ファイルのコミットにより、同じ記事・同じトピックの重複通知を防ぎます

## カスタマイズ

- **情報源の追加・削除**: `config/sources.yaml` の `sources` を編集（`rss` または `google_news`）
- **一般メディアの事前フィルタ**: `keywords` を編集
- **公式ドメインの追加**: `official_domains` にドメインと tier を追加
- **判定基準の調整**: `adbot/evaluator.py` の `SYSTEM_PROMPT` を編集

## 開発

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 注意事項

- 取得に失敗した情報源はスキップします（1 つの失敗で全体は止まりません）。フィード URL は媒体側の変更で無効になることがあるので、レポートの「取得に失敗した情報源」を確認してください。
- Claude の評価に失敗した記事は「評価済み」にせず、次回実行時に再評価します。
- Claude API の利用料が発生します。1回あたりの評価記事数は `--max-articles` で上限を設定できます。
