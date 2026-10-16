"""agent3 for a non-AI topic (the shared "demo" topic: two sections, one variant, no paper spotlight),
local mode with a scripted client. The AI newsletter's output is pinned by test_topics_ai_equivalence.py
and the other agent3 tests."""
import json
from types import SimpleNamespace

import pytest

import agent2b_summarize_news as a2b
import agent3_compose as a3
import tracing


class Client:
    """Selection picks the first three articles; dedup finds nothing; the intro is a fixed sentence."""

    def __init__(self):
        self.messages = self
        self.prompts = {"selection": [], "intro": []}

    def create(self, **kw):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1)
        names = [t["name"] for t in kw.get("tools") or []]
        if "report_duplicates" in names:
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t", name="report_duplicates",
                                                            input={"groups": []})], usage=usage)
        prompt = kw["messages"][0]["content"]
        if "indices" in prompt:
            self.prompts["selection"].append(prompt)
            body = json.dumps({"selected": [0, 1, 2], "runners_up": [3]})
        else:
            self.prompts["intro"].append(prompt)
            body = "A quiet week, with one big story."
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], usage=usage)


def _article(i, section):
    return {"title": f"{section} story {i}", "url": f"https://x.example/{section}/{i}",
            "summary": f"About {section} {i}", "language": "en", "hn_score": 40 - i if i == 0 else None}


@pytest.fixture
def composed(tmp_path, monkeypatch, demo_topic):
    news = {"by_category": {"Alpha": [_article(i, "Alpha") for i in range(4)],
                            "Beta": [_article(i, "Beta") for i in range(4)]}}
    (tmp_path / "paper_summaries.json").write_text(json.dumps({"papers": []}), encoding="utf-8")
    (tmp_path / "news_summaries.json").write_text(json.dumps(news), encoding="utf-8")
    client = Client()
    monkeypatch.setenv("TOPIC", "demo")
    monkeypatch.setenv("MAILING_ADDRESS", "1 Example St")
    monkeypatch.setattr(a3, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(a3, "USE_FIRESTORE", False)
    monkeypatch.setattr(tracing, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(a3.random.Random, "shuffle", lambda self, x: None)
    a3.run("r1")
    return tmp_path, client


def test_only_the_topics_variants_are_written(composed):
    tmp, _ = composed
    assert (tmp / "newsletter_0_0.html").exists()
    for other in ("1_0", "0_1", "1_1"):
        assert not (tmp / f"newsletter_{other}.html").exists()


def test_html_carries_the_topics_name_tagline_and_accent(composed):
    html = (composed[0] / "newsletter_0_0.html").read_text(encoding="utf-8")
    assert "<title>Demo Weekly</title>" in html and "A demo" in html
    assert "#112233" in html
    assert "Latent SpaceMail" not in html and "Weekly AI Intelligence Dispatch" not in html
    assert "logo-email.png" not in html                      # the AI brand's logo
    assert "c8b89a" not in html                              # the AI accent is not hardcoded anywhere


def test_html_has_only_the_topics_sections_and_no_spotlight_or_share_strip(composed):
    html = (composed[0] / "newsletter_0_0.html").read_text(encoding="utf-8")
    assert "<!-- SECTION:Alpha -->" in html and "<!-- SECTION:Beta -->" in html
    assert "Research Spotlights" not in html and "RESEARCH" not in html and "<!-- SHARE -->" not in html
    assert "Model & Product" not in html and "Canada" not in html


def test_section_caps_come_from_the_profile(composed):
    html = (composed[0] / "newsletter_0_0.html").read_text(encoding="utf-8")
    alpha = html.split("<!-- SECTION:Alpha -->")[1].split("<!-- /SECTION:Alpha -->")[0]
    beta = html.split("<!-- SECTION:Beta -->")[1].split("<!-- /SECTION:Beta -->")[0]
    import re
    links = lambda part: set(re.findall(r'href="(https://x\.example/[^"]+)"', part))
    assert len(links(alpha)) == 3 and len(links(beta)) == 2            # the default cap of 3 vs Beta's cap of 2
    assert "https://x.example/Beta/2" not in beta


def test_agent4_placeholders_survive(composed):
    html = (composed[0] / "newsletter_0_0.html").read_text(encoding="utf-8")
    assert "{{UNSUBSCRIBE_URL}}" in html and "{{PREFERENCES_URL}}" in html
    assert "1 Example St" in html


def test_prompts_are_the_generic_ones_filled_for_the_topic(composed):
    _, client = composed
    assert client.prompts["selection"] and client.prompts["intro"]
    sel = client.prompts["selection"][0]
    assert 'editor of "Demo Weekly"' in sel and "demo readers" in sel
    assert "at most 3" in sel and "at most 2" in client.prompts["selection"][1]    # per-section caps
    assert "{" not in sel.replace('{"selected"', "")                               # nothing left unfilled
    intro = client.prompts["intro"][0]
    assert 'editor of "Demo Weekly"' in intro and "Alpha story 0" in intro


def test_empty_section_uses_the_generic_text(tmp_path, monkeypatch, demo_topic):
    html = a3.compose_html("Intro", [], {"Alpha": [], "Beta": []}, "wk", topic=demo_topic)
    assert "Nothing notable this week." in html and "No notable releases" not in html


def test_run_doc_update_uses_the_topics_sections(demo_topic):
    shipped = {"Alpha": [{"url": "https://a"}], "Beta": [], "Industry & Business": [{"url": "https://zzz"}]}
    upd = a3.build_run_doc_update({"0_0": "h"}, "Demo Weekly — wk", shipped, shipped, {"Alpha": [1]}, topic=demo_topic)
    assert list(upd["news_summaries"]) == ["Alpha"]
    assert upd["newsletter_subject"] == "Demo Weekly — wk" and upd["newsletter_html"] == "h"


def test_link_colour_is_a_darker_shade_of_the_accent(demo_topic):
    html = a3.compose_html("Intro", [], {"Alpha": [_article(0, "Alpha")], "Beta": []}, "wk", topic=demo_topic)
    assert a3._darken("#112233") == "#081119"
    assert "color:#081119;text-decoration:underline" in html


def test_agent2b_uses_the_topics_summary_prompts(monkeypatch, tmp_path, demo_topic):
    monkeypatch.setenv("TOPIC", "demo")
    monkeypatch.setattr(a2b, "USE_FIRESTORE", False)
    monkeypatch.setattr(a2b, "DATA_DIR", str(tmp_path))
    articles = [{"title": "Alpha story", "description": "d", "url": "https://x.example/1", "language": "en",
                 "source": "rss", "hn_score": None, "category": "Alpha"}]
    (tmp_path / "news_filtered.json").write_text(json.dumps({"by_category": {"Alpha": articles}}), encoding="utf-8")
    seen = []

    class C:
        messages = None

        def create(self, **kw):
            seen.append(kw["messages"][0]["content"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="A summary.")],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    C.messages = C()
    monkeypatch.setattr(a2b.tracing, "make_client", lambda *a, **k: C())
    monkeypatch.setattr(a2b, "fetch_article_text", lambda url, *a, **k: "Full article text here.")
    a2b.run("r1")
    assert seen and 'for "Demo Weekly"' in seen[0] and "demo readers" in seen[0]
    out = json.loads((tmp_path / "news_summaries.json").read_text(encoding="utf-8"))
    assert out["by_category"]["Alpha"][0]["summary"] == "A summary."
