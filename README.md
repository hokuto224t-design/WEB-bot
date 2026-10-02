# WEB-bot

WEB広告・デジタルマーケティングの最新情報を収集し、**重要な情報があるときだけ** Slack に通知する Bot。

- 広告媒体の公式発表・公式ブログ・国内外のマーケティングメディアを RSS で毎朝収集
- Claude（`claude-opus-5-5`）が「広告運用担当者が知るべき情報か」を判定
- 重要度 4 以上（5段階）のものだけを Slack に通知。**該当がない日は何も送信しない**
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

## セットアップ

### 1. Slack Incoming Webhook を作成

1. https://api.slack.com/apps で App を作成 → **Incoming Webhooks** を有効化
2. 通知先チャンネルを選んで Webhook URL を発行

### 2. GitHub Actions で毎日実行（推奨）

1. リポジトリの **Settings → Secrets and variables → Actions** に以下を登録
   - `ANTHROPIC_API_KEY`: Claude API キー
   - `SLACK_WEBHOOK_URL`: 上記の Webhook URL
2. `.github/workflows/daily.yml` が毎日 **7:47 JST** に実行されます
3. 初回は **Actions → Daily ad news check → Run workflow** で `dry_run` にチェックを入れて動作確認できます

実行後、`data/state.json`（評価済み・通知済みの記録）が自動でコミットされます。

> 初回実行時は直近72時間の記事がまとめて評価されるため、通知がやや多くなる可能性があります。

### 3. ローカルで実行

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

python -m adbot.main --dry-run   # Slack に送らず判定結果だけ表示（状態も保存しない）
python -m adbot.main             # 本番実行
```

主なオプション:

| オプション | 既定値 | 説明 |
|---|---|---|
| `--since-hours` | 72 | この時間以内に公開された記事を対象にする |
| `--min-importance` | 4 | 通知する最低重要度（1-5）。通知を減らしたいときは 5 |
| `--max-items` | 10 | 1回の通知の最大件数 |
| `--max-articles` | 150 | 1回に AI 評価する最大記事数（超過分は次回評価） |
| `--model` | `claude-opus-5-5` | 使用モデル（環境変数 `ADBOT_MODEL` でも指定可） |

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

- 取得に失敗した情報源はスキップしてログに警告を出します（1 つの失敗で全体は止まりません）。フィード URL は媒体側の変更で無効になることがあるので、Actions のログで `ソース取得失敗` を定期的に確認してください。
- Claude の評価に失敗した記事は「評価済み」にせず、次回実行時に再評価します。
- Claude API の利用料が発生します。1回あたりの評価記事数は `--max-articles` で上限を設定できます。
