"""agent3 produces the topic's widgets, stores them and places them; a failing widget is left out and never fails
the run. The AI email stays byte-identical (test_render_goldens.py, test_widget_paper_spotlight.py)."""
import json

import pytest

import agent3_compose as a3
import topics
import tracing
from fakes_firestore import FakeDb
from test_agent3_topic import Client, _article
from widgets import registry
from widgets.base import Widget


class _Good(Widget):
    id = "t_good"
    name = "Good widget"
    description = "test"

    def produce(self, ctx, config):
        return {"text": f"good for {ctx.topic}, {len(ctx.picks.get('Alpha', []))} picks, week {ctx.week}"}

    def render(self, data, config, look):
        return f"<!-- WIDGET:t_good --><tr><td>{data['text']}</td></tr><!-- /WIDGET:t_good -->"

    def sample_data(self):
        return {"text": "sample"}


class _Bad(_Good):
    id = "t_bad"

    def produce(self, ctx, config):
        raise RuntimeError("source down")


@pytest.fixture
def widget_topic(monkeypatch, demo_topic):
    for cls in (_Good, _Bad):
        registry.register(cls)
    raw = {"id": "demo", "name": "Demo Weekly", "tagline": "A demo", "audience": "demo readers",
           "accent_color": "#112233", "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
           "categories": [{"name": "Alpha", "definition": "first"}, {"name": "Beta", "definition": "second"}],
           "sources": [{"type": "rss", "feeds": [{"name": "f", "url": "https://example.com/f.xml"}]}],
           "widgets": [{"id": "t_good", "placement": "after_sections"}, {"id": "t_bad", "placement": "after_intro"}]}
    monkeypatch.setitem(topics._CACHE, "demo", topics.parse_topic(raw))
    yield
    for cls in (_Good, _Bad):
        registry.unregister(cls.id)


def _setup(tmp_path, monkeypatch):
    news = {"by_category": {"Alpha": [_article(i, "Alpha") for i in range(4)],
                            "Beta": [_article(i, "Beta") for i in range(4)]}}
    (tmp_path / "paper_summaries.json").write_text(json.dumps({"papers": []}), encoding="utf-8")
    (tmp_path / "news_summaries.json").write_text(json.dumps(news), encoding="utf-8")
    client = Client()
    monkeypatch.setenv("TOPIC", "demo")
    monkeypatch.setattr(a3, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tracing, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(a3.random.Random, "shuffle", lambda self, x: None)
    return news


def test_local_run_places_the_good_widget_and_drops_the_bad_one(tmp_path, monkeypatch, widget_topic):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(a3, "USE_FIRESTORE", False)
    a3.run("r1")
    html = (tmp_path / "newsletter_0_0.html").read_text(encoding="utf-8")
    assert "good for demo, 3 picks, week " in html and "t_bad" not in html
    # after_sections: below the last section, above the footer
    assert html.index("<!-- /SECTION:Beta -->") < html.index("<!-- WIDGET:t_good -->")
    summary = json.loads((tmp_path / "widgets" / "summary.json").read_text(encoding="utf-8"))
    assert summary == {"t_good": "ok", "t_bad": "failed"}
    edition = json.loads((tmp_path / "edition.json").read_text(encoding="utf-8"))
    assert edition["widgets"] == ["t_good"]


def test_cloud_run_writes_widgets_summary_and_widget_docs(tmp_path, monkeypatch, widget_topic):
    news = _setup(tmp_path, monkeypatch)
    import google.cloud.firestore as fs
    db = FakeDb()
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(a3, "USE_FIRESTORE", True)
    db.collection(a3.FIRESTORE_COLLECTION).document("r1").set(
        {"topic": "demo", "paper_summaries": [], "news_summaries": news["by_category"], "run_kind": "debug",
         "started_at": "2026-10-18T16:00:00+00:00"})
    a3.run("r1")
    run = db.store[a3.FIRESTORE_COLLECTION]["r1"]
    assert run["widgets_summary"] == {"t_good": "ok", "t_bad": "failed"}
    assert run["newsletter_composed"] is True and "WIDGET:t_good" in run["newsletter_variants"]["0_0"]
    docs = db.store[f"{a3.FIRESTORE_COLLECTION}/r1/widgets"]
    assert docs["t_good"]["status"] == "ok" and docs["t_bad"]["error"].startswith("RuntimeError: source down")
    assert db.store["editions"]["r1"]["widgets"] == ["t_good"]


def test_widget_crash_inside_the_runner_never_fails_the_run(tmp_path, monkeypatch, widget_topic):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(a3, "USE_FIRESTORE", False)
    import widgets.edition as we
    monkeypatch.setattr(we, "produce_edition_widgets", lambda *a, **k: 1 / 0)
    a3.run("r1")
    assert "WIDGET" not in (tmp_path / "newsletter_0_0.html").read_text(encoding="utf-8")


def test_ai_profile_definition_is_unchanged():
    import newsletter_def
    d = newsletter_def.from_topic(topics.get_topic("ai"))
    assert [b.type for b in d.blocks[:3]] == ["intro", "widget", "section"]
    assert d.blocks[1].widget == "paper_spotlight" and d.blocks[3].type == "share"
