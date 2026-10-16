"""Editions (contract C2): what agent3 saves per run, the size guard, the lookups, and agent3's best-effort write."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import agent3_compose as a3
import editions
import tracing
from fakes_firestore import FakeDb

NOW = datetime(2026, 10, 18, 16, 40, 12, tzinfo=timezone.utc)


def _a(i, lang="en", hn=None, summary=True, **extra):
    return {"title": f"Story {i}", "url": f"https://x.example/{i}", "summary": f"About {i}." if summary else "",
            "description": "desc", "language": lang, "hn_score": hn, "source": "rss", "used_fallback": False,
            "hn_rank": 3, **extra}


def _edition(demo_topic, **kw):
    args = dict(topic=demo_topic, run_id="R1", run_kind="release", week_of="2026-10-19", intro="Hello.",
                selected_all={"Alpha": [_a(1, hn=50), _a(2, lang="fr")], "Beta": []},
                selected_en={"Alpha": [_a(1, hn=50), _a(3)], "Beta": []},
                pools={"Alpha": [_a(2, lang="fr"), _a(4), _a(5, summary=False), _a(4), _a(3), _a(6)]},
                widget_ids=[], now=NOW)
    args.update(kw)
    return editions.build_edition(**args)


def test_edition_follows_the_contract(demo_topic):
    e = _edition(demo_topic)
    assert {k: e[k] for k in ("topic", "run_id", "run_kind", "week_of", "intro", "widgets")} == {
        "topic": "demo", "run_id": "R1", "run_kind": "release", "week_of": "2026-10-19", "intro": "Hello.",
        "widgets": []}
    assert e["created_at"] == "2026-10-18T16:40:12Z"
    assert (e["expires_at"] - NOW).days == editions.KEEP_DAYS
    assert list(e["sections"]) == ["Alpha", "Beta"]
    alpha = e["sections"]["Alpha"]
    assert alpha["icon"] == "A"
    assert set(alpha["picks"][0]) == set(editions.ARTICLE_FIELDS)
    assert alpha["picks"][0]["points"] == 50 and alpha["picks"][0]["points_source"] == "hn"
    assert alpha["picks"][1]["points"] is None and alpha["picks"][1]["points_source"] is None
    assert alpha["picks"][0]["published_at"] is None


def test_picks_en_only_when_it_differs(demo_topic):
    e = _edition(demo_topic)
    assert [a["url"] for a in e["sections"]["Alpha"]["picks_en"]] == ["https://x.example/1", "https://x.example/3"]
    assert "picks_en" not in e["sections"]["Beta"]


def test_runners_up_are_summarized_unshipped_and_unique(demo_topic):
    alpha = _edition(demo_topic)["sections"]["Alpha"]
    assert [a["url"] for a in alpha["runners_up"]] == ["https://x.example/4", "https://x.example/6"]
    assert _edition(demo_topic)["sections"]["Beta"]["runners_up"] == []


def test_unsummarized_picks_are_left_out(demo_topic):
    e = _edition(demo_topic, selected_all={"Alpha": [_a(1, summary=False), _a(2)]}, selected_en={})
    assert [a["url"] for a in e["sections"]["Alpha"]["picks"]] == ["https://x.example/2"]


def test_size_guard_trims_runners_up(demo_topic, monkeypatch):
    monkeypatch.setattr(editions, "MAX_EDITION_BYTES", 4000)
    big = [_a(i, summary=True) | {"summary": "x" * 300} for i in range(10, 30)]
    e = _edition(demo_topic, pools={"Alpha": big[:12], "Beta": big[12:]})
    assert editions._size(e) <= 4000
    assert e["sections"]["Alpha"]["picks"]          # picks are never trimmed
    kept = len(e["sections"]["Alpha"]["runners_up"]) + len(e["sections"]["Beta"]["runners_up"])
    assert 0 < kept < 20


def _store(*docs):
    db = FakeDb()
    for d in docs:
        db.store.setdefault("editions", {})[d["run_id"]] = d
    return db


def _doc(run_id, topic="finance", kind="release", created="2026-10-18T16:00:00Z", week="2026-10-19"):
    return {"run_id": run_id, "topic": topic, "run_kind": kind, "created_at": created, "week_of": week}


def test_save_and_latest_release_of_a_topic(demo_topic):
    db = FakeDb()
    editions.save(db, _edition(demo_topic))
    assert db.store["editions"]["R1"]["topic"] == "demo"
    db = _store(_doc("old", created="2026-10-11T16:00:00Z", week="2026-10-12"), _doc("new"),
                _doc("dbg", kind="debug", created="2026-10-19T10:00:00Z"),
                _doc("other", topic="gaming", created="2026-10-19T11:00:00Z"))
    assert editions.latest(db, "finance")["run_id"] == "new"
    assert editions.latest(db, "gaming")["run_id"] == "other"
    assert editions.latest(db, "boardgames") is None


def test_for_week(demo_topic):
    db = _store(_doc("a", created="2026-10-18T10:00:00Z"), _doc("b", created="2026-10-18T12:00:00Z"),
                _doc("c", created="2026-10-11T12:00:00Z", week="2026-10-12"),
                _doc("d", kind="debug", created="2026-10-18T13:00:00Z"))
    assert editions.for_week(db, "finance", "2026-10-19")["run_id"] == "b"
    assert editions.for_week(db, "finance", "2026-10-12")["run_id"] == "c"
    assert editions.for_week(db, "finance", "2026-10-26") is None


# --- agent3 writes the edition ------------------------------------------------------------------------------

class Client:
    """Selection picks the first two and names the third a runner-up; dedup reports article 1 and 2 as one story."""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1)
        names = [t["name"] for t in kw.get("tools") or []]
        if "report_duplicates" in names:
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t", name="report_duplicates",
                                                            input={"groups": []})], usage=usage)
        prompt = kw["messages"][0]["content"]
        body = json.dumps({"selected": [0, 1], "runners_up": [2]}) if "indices" in prompt else "Intro text."
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], usage=usage)


def _news():
    return {"Alpha": [_a(i) for i in range(1, 6)], "Beta": [_a(i) for i in range(11, 14)]}


@pytest.fixture
def agent3_env(monkeypatch, tmp_path, demo_topic):
    monkeypatch.setattr(a3, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tracing, "make_client", lambda *a, **k: Client())
    monkeypatch.setattr(a3.random.Random, "shuffle", lambda self, x: None)
    return tmp_path


def test_agent3_local_run_writes_data_edition_json(agent3_env, monkeypatch):
    monkeypatch.setenv("TOPIC", "demo")
    monkeypatch.setattr(a3, "USE_FIRESTORE", False)
    (agent3_env / "paper_summaries.json").write_text(json.dumps({"papers": []}), encoding="utf-8")
    (agent3_env / "news_summaries.json").write_text(json.dumps({"by_category": _news()}), encoding="utf-8")
    a3.run("local-1")
    e = json.loads((agent3_env / "edition.json").read_text(encoding="utf-8"))
    assert e["topic"] == "demo" and e["run_kind"] == "debug" and e["intro"] == "Intro text."
    alpha = e["sections"]["Alpha"]
    assert len(alpha["picks"]) == 2 and alpha["runners_up"]
    assert not {a["url"] for a in alpha["picks"]} & {a["url"] for a in alpha["runners_up"]}


@pytest.fixture
def cloud(agent3_env, monkeypatch):
    import google.cloud.firestore as fs
    db = FakeDb({"pipeline_runs": {"R9": {"topic": "demo", "run_kind": "release", "paper_summaries": [],
                                           "news_summaries": _news()}}})
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(a3, "USE_FIRESTORE", True)
    return db


def test_agent3_cloud_run_saves_the_edition_before_pruning(cloud):
    a3.run("R9")
    e = cloud.store["editions"]["R9"]
    assert e["run_kind"] == "release" and e["topic"] == "demo"
    assert sum(len(s["runners_up"]) for s in e["sections"].values()) > 0   # from the full pools
    run = cloud.store["pipeline_runs"]["R9"]
    assert run["newsletter_composed"] and "agent3_edition_error" not in run


def test_a_good_recompose_clears_an_old_edition_error(cloud):
    cloud.store["pipeline_runs"]["R9"]["agent3_edition_error"] = "old"
    a3.run("R9")
    assert "agent3_edition_error" not in cloud.store["pipeline_runs"]["R9"]


def test_agent3_debug_run_edition_is_debug(cloud):
    cloud.store["pipeline_runs"]["R9"]["run_kind"] = "debug"
    a3.run("R9")
    assert cloud.store["editions"]["R9"]["run_kind"] == "debug"


def test_edition_save_failure_does_not_fail_the_run(cloud, monkeypatch):
    def boom(db, edition):
        raise RuntimeError("firestore said no")
    monkeypatch.setattr(a3.editions, "save", boom)
    a3.run("R9")
    run = cloud.store["pipeline_runs"]["R9"]
    assert run["newsletter_composed"] and run["agent3_edition_error"] == "firestore said no"
    assert "agent3_error" not in run and "editions" not in cloud.store


def test_dedup_duplicates_stay_out_of_the_runners_up():
    audits = [{"category": "Alpha", "groups": [{"kept": {"url": "k"}, "removed": [{"url": "r1"}]}],
               "fallbacks": [{"url": "f1", "accepted": False}, {"url": "f2", "accepted": True}]},
              {"category": "Beta", "groups": [{"removed": [{"url": "b"}]}], "fallbacks": []}]
    assert a3._duplicate_urls(audits, "Alpha") == {"r1", "f1"}
