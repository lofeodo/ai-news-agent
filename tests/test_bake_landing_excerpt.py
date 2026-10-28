"""tools/bake_landing_excerpt.py: parse a composed issue, bake it into the landing page, never half-write."""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "tools" / "bake_landing_excerpt.py"
_spec = importlib.util.spec_from_file_location("bake_landing_excerpt", _PATH)
bake_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bake_mod)

PAPER_SUMMARY = "An RL-trained teacher encodes not just a destination but a direction for learning, the paper shows."
BODY = "Cactus released a 16.9 MB speech-to-text model that runs entirely on-device with no dependencies at all."


def _issue(stories=3, paper=True, date="October 12, 2026"):
    """HTML shaped like the composed email: preheader padding, TOC, editor's note, paper card, sections."""
    parts = [
        "<html><head><title>Latent SpaceMail</title><style>.x{color:red}</style></head><body>",
        "<div>A preheader that goes on‌ ‌ ‌ ‌</div>",
        f"<table><tr><td>Weekly AI Intelligence Dispatch</td><td>{date}</td></tr></table>",
        "<div>01</div><div> MODEL</div><div>02</div><div> POLICY</div>",  # TOC: short text, must be skipped
        "<div>&gt;_ EDITOR'S NOTE ··· October 12, 2026</div><p>An editor's note that is long enough to be ignored.</p>",
    ]
    if paper:
        parts += [
            "<div>★  Paper of the week</div><div>▲ 515</div><div> upvotes</div>",
            "<h2>The Teacher Is a Direction</h2><p>Hao Li et al.</p>",
            f"<p>{PAPER_SUMMARY}</p><a>Read on Hugging Face  →</a>",
        ]
    for i in range(1, stories + 1):
        parts += [
            f"<div>0{i}</div><div>🚀  Section {i} &amp; Co</div>",
            f"<h3>Story title {i} <script>alert(1)</script></h3><p>{BODY} {i}</p>",
            "<span>HN Score</span><span>▲ 1,118</span><a>Read →</a>",
            f"<h3>Second story of section {i}</h3><p>{BODY} second</p><a>Read →</a>",
        ]
    parts.append("</body></html>")
    return "".join(parts)


def test_parse_extracts_paper_and_first_story_per_section():
    data = bake_mod.parse_issue(_issue())
    assert data["date"] == "October 12, 2026"
    assert data["paper"] == {"votes": "▲ 515", "title": "The Teacher Is a Direction",
                             "by": "Hao Li et al.", "summary": PAPER_SUMMARY}
    assert [s["n"] for s in data["stories"]] == ["01", "02", "03"]
    first = data["stories"][0]
    assert first["section"] == "Section 1 & Co"            # leading emoji stripped
    assert first["title"].startswith("Story title 1")
    assert first["score"] == "▲ 1,118"
    assert all("Second story" not in s["title"] for s in data["stories"])


def test_parse_keeps_at_most_three_stories():
    assert len(bake_mod.parse_issue(_issue(stories=5))["stories"]) == 3


def test_parse_accepts_two_stories_but_not_one():
    assert len(bake_mod.parse_issue(_issue(stories=2))["stories"]) == 2
    with pytest.raises(ValueError, match="story"):
        bake_mod.parse_issue(_issue(stories=1))


def test_parse_rejects_issue_without_paper_card():
    with pytest.raises(ValueError, match="paper card"):
        bake_mod.parse_issue(_issue(paper=False))


def test_render_escapes_markup_from_the_issue():
    data = bake_mod.parse_issue(_issue())
    data["stories"][0]["title"] = "<img src=x onerror=alert(1)> & more"
    out = bake_mod.render_excerpt(data)
    assert "<img" not in out
    assert "&lt;img src=x onerror=alert(1)&gt; &amp; more" in out


INDEX = "<div class='issue-wrap'>\n        <!-- EXCERPT:START -->\n        old\n        <!-- EXCERPT:END -->\n      </div>"


def test_bake_replaces_only_between_markers_and_is_idempotent():
    data = bake_mod.parse_issue(_issue())
    once = bake_mod.bake(INDEX, data)
    assert "old" not in once
    assert once.startswith("<div class='issue-wrap'>") and once.endswith("</div>")
    assert "Story title 1" in once
    assert bake_mod.bake(once, data) == once


@pytest.mark.parametrize("bad", ["no markers", INDEX + INDEX])
def test_bake_requires_exactly_one_marker_pair(bad):
    with pytest.raises(ValueError, match="marker"):
        bake_mod.bake(bad, bake_mod.parse_issue(_issue()))


def test_main_failure_leaves_index_untouched(tmp_path, capsys):
    index = tmp_path / "index.html"
    index.write_text(INDEX, encoding="utf-8")
    issue = tmp_path / "issue.html"
    issue.write_text(_issue(paper=False), encoding="utf-8")
    assert bake_mod.main(["--source", str(issue), "--index", str(index)]) == 1
    assert index.read_text(encoding="utf-8") == INDEX
    assert "FAILED" in capsys.readouterr().err


def test_main_success_writes_index(tmp_path):
    index = tmp_path / "index.html"
    index.write_text(INDEX, encoding="utf-8")
    issue = tmp_path / "issue.html"
    issue.write_text(_issue(), encoding="utf-8")
    assert bake_mod.main(["--source", str(issue), "--index", str(index)]) == 0
    assert "The Teacher Is a Direction" in index.read_text(encoding="utf-8")


def test_render_ends_with_a_button_to_the_full_issue():
    out = bake_mod.render_excerpt(bake_mod.parse_issue(_issue()))
    assert '<a class="issue__open" href="/issues.html">Read the full issue' in out
    assert out.index("issue__open") < out.index("</figure>")
