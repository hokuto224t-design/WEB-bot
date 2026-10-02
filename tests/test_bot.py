import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from adbot import main as main_mod
from adbot.collector import collect, matches_keywords, official_tier, parse_feed
from adbot.evaluator import Evaluator, parse_evaluations
from adbot.models import Article, Evaluation, normalize_url
from adbot.selection import select_notifications
from adbot.slack import build_messages
from adbot.store import StateStore

NOW = datetime.now(timezone.utc)
RFC822 = "%a, %d %b %Y %H:%M:%S +0000"

CONFIG = {
    "keywords": ["広告", "ads", "Performance Max"],
    "official_domains": {"blog.google": 1, "lycbiz.com": 1, "facebook.com/business": 2},
    "social_domains": ["x.com", "reddit.com"],
}


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{d}</description>"
        f"<pubDate>{p.strftime(RFC822)}</pubDate>{extra}</item>"
        for t, link, d, p, extra in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


def article(url, tier=1, social=False, title="t"):
    return Article(title=title, url=url, source_name="s", tier=tier, is_social=social, published=NOW)


def evaluation(art, topic, importance=4, notify=True, relevance="high"):
    return Evaluation(
        article_id=art.id, notify=notify, importance=importance, category="媒体アップデート",
        platform="Google Ads", title_ja="タイトル", summary_ja="概要", impact="影響", action="対応",
        topic_key=topic, japan_relevance=relevance, reason="理由",
    )


# ---------- collector ----------

def test_normalize_url_strips_tracking():
    assert normalize_url("https://Example.com/a/?utm_source=x&id=1#frag") == "https://example.com/a?id=1"


def test_official_tier_matches_subdomain_and_path():
    assert official_tier("https://www.lycbiz.com/jp/news/1", CONFIG["official_domains"]) == 1
    assert official_tier("https://www.facebook.com/business/news/x", CONFIG["official_domains"]) == 2
    assert official_tier("https://www.facebook.com/someone", CONFIG["official_domains"]) is None


def test_keyword_filter_uses_word_boundary_for_short_terms():
    assert matches_keywords(article("https://e.com", title="New Google Ads feature"), ["ads"])
    assert not matches_keywords(article("https://e.com", title="Roads and loads"), ["ads"])
    assert matches_keywords(article("https://e.com", title="P-MAXと広告の新機能"), ["広告"])


def test_parse_google_news_upgrades_tier_and_strips_publisher():
    content = rss([(
        "Yahoo!広告 新機能 - LINEヤフー for Business", "https://news.google.com/rss/articles/abc",
        "d", NOW, '<source url="https://www.lycbiz.com">LINEヤフー for Business</source>',
    )])
    source = {"name": "g", "type": "google_news", "tier": 4, "query": "q"}
    [a] = parse_feed(content, source, CONFIG)
    assert a.title == "Yahoo!広告 新機能"
    assert a.tier == 1
    assert a.publisher == "LINEヤフー for Business"


def test_collect_filters_old_keyword_and_failures():
    feeds = {
        "https://ok/feed": rss([
            ("Performance Max update", "https://blog.google/a", "x", NOW, ""),
            ("Old ads news", "https://blog.google/old", "x", NOW - timedelta(days=10), ""),
            ("Company earnings", "https://blog.google/earn", "x", NOW, ""),
        ]),
    }

    class FakeSession:
        def get(self, url, **_):
            if url not in feeds:
                raise ConnectionError("down")
            return SimpleNamespace(content=feeds[url], raise_for_status=lambda: None)

    config = dict(CONFIG, sources=[
        {"name": "ok", "type": "rss", "url": "https://ok/feed", "tier": 3, "keyword_filter": True},
        {"name": "down", "type": "rss", "url": "https://down/feed", "tier": 1},
    ])
    result = collect(config, since=NOW - timedelta(days=3), session=FakeSession())
    assert [a.title for a in result] == ["Performance Max update"]
    assert result[0].tier == 1  # blog.google は公式ドメイン


# ---------- selection ----------

def test_selection_groups_topics_and_prefers_primary_source():
    official = article("https://blog.google/pmax", tier=1)
    media = article("https://media.example/pmax", tier=4)
    evs = [evaluation(media, "google-pmax-x", importance=5), evaluation(official, "google-pmax-x", importance=4)]
    [n] = select_notifications([official, media], evs, set())
    assert n.primary is official
    assert n.related == [media]
    assert n.evaluation.importance == 5


def test_selection_safety_rules():
    low = article("https://a.example/1")
    not_notify = article("https://a.example/2")
    dup = article("https://a.example/3")
    social = article("https://x.com/u/status/1", tier=4, social=True)
    overseas = article("https://b.example/1", tier=5)
    evs = [
        evaluation(low, "low", importance=3),
        evaluation(not_notify, "nn", notify=False, importance=5),
        evaluation(dup, "already-sent"),
        evaluation(social, "rumor", importance=5),
        evaluation(overseas, "us-only", relevance="low"),
    ]
    arts = [low, not_notify, dup, social, overseas]
    assert select_notifications(arts, evs, {"already-sent"}) == []


# ---------- evaluator ----------

def test_parse_evaluations_drops_unknown_and_duplicates():
    a = article("https://blog.google/a")
    item = {
        "id": a.id, "notify": True, "importance": 9, "category": "媒体アップデート", "platform": "Google Ads",
        "title_ja": "t", "summary_ja": "s", "impact": "i", "action": "a", "topic_key": " Key ",
        "japan_relevance": "high", "duplicate_of_notified": True, "reason": "r",
    }
    [ev] = parse_evaluations({"evaluations": [item, dict(item, id="unknown")]}, {a.id})
    assert ev.importance == 5 and ev.topic_key == "key" and ev.notify is False


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def fake_client(text, stop_reason="end_turn"):
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        msg = SimpleNamespace(
            stop_reason=stop_reason, stop_details=None,
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        )
        return FakeStream(msg)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    return client, calls


def eval_json(arts, notify=True):
    return json.dumps({"evaluations": [{
        "id": a.id, "notify": notify, "importance": 5, "category": "媒体アップデート", "platform": "Google Ads",
        "title_ja": "P-MAX の新機能", "summary_ja": "s", "impact": "i", "action": "a",
        "topic_key": f"topic-{a.id}", "japan_relevance": "high", "duplicate_of_notified": False, "reason": "r",
    } for a in arts]})


def test_evaluator_batches_and_sets_request_options():
    arts = [article(f"https://blog.google/{i}") for i in range(3)]
    client, calls = fake_client(eval_json(arts))
    evs, ids = Evaluator(client=client, batch_size=2).evaluate(arts, [])
    assert len(calls) == 2
    assert calls[0]["model"] == "claude-opus-5-5"
    assert calls[0]["fallbacks"] == "default"
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert ids == {a.id for a in arts} and len(evs) == 3


def test_evaluator_failed_batch_is_not_marked_evaluated():
    arts = [article("https://blog.google/a")]
    client, _ = fake_client("", stop_reason="refusal")
    evs, ids = Evaluator(client=client).evaluate(arts, [])
    assert evs == [] and ids == set()


# ---------- slack / store ----------

def test_build_messages_splits_over_block_limit():
    arts = [article(f"https://blog.google/{i}") for i in range(20)]
    notifications = select_notifications(arts, [evaluation(a, f"t{i}") for i, a in enumerate(arts)], set(), max_items=20)
    messages = build_messages(notifications)
    assert len(messages) == 2
    assert all(len(m["blocks"]) <= 50 for m in messages)
    assert messages[0]["blocks"][0]["type"] == "header"


def test_store_roundtrip_and_prune(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path, seen_days=1)
    store.mark_seen(["a"])
    store.seen["old"] = (NOW - timedelta(days=5)).isoformat()
    store.add_notified("k", "title", "https://e")
    store.save()
    loaded = StateStore(path)
    assert loaded.is_seen("a") and not loaded.is_seen("old")
    assert loaded.notified_topic_keys() == {"k"}


# ---------- end-to-end ----------

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    arts = [article("https://blog.google/pmax", title="Performance Max update")]
    monkeypatch.setattr(main_mod, "collect", lambda config, since: arts)
    posted = []
    monkeypatch.setattr(main_mod.slack, "post", lambda url, ns: posted.append(ns))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    args = main_mod.parse_args(["--state", str(tmp_path / "state.json")])
    return arts, posted, args, monkeypatch


def test_run_posts_only_when_important_and_never_twice(run_env):
    arts, posted, args, monkeypatch = run_env
    client, _ = fake_client(eval_json(arts))
    monkeypatch.setattr(main_mod, "Evaluator", lambda model: Evaluator(client=client, model=model))
    assert main_mod.run(args) == 0
    assert len(posted) == 1
    # 2 回目は評価済みなので何も送らない
    assert main_mod.run(args) == 0
    assert len(posted) == 1


def test_run_sends_nothing_when_no_important_news(run_env):
    arts, posted, args, monkeypatch = run_env
    client, _ = fake_client(eval_json(arts, notify=False))
    monkeypatch.setattr(main_mod, "Evaluator", lambda model: Evaluator(client=client, model=model))
    assert main_mod.run(args) == 0
    assert posted == []
