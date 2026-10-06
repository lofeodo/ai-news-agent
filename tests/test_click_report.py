"""Click report formatting. Pure."""
import click_links as cl
import click_report as cr

A, B, C = "https://e.com/a", "https://e.com/b", "https://e.com/c"
LINKS = {
    cl.url_key(A): {"url": A, "title": "Alpha", "category": "Industry & Business"},
    cl.url_key(B): {"url": B, "title": "Beta", "category": "Industry & Business"},
    cl.url_key(C): {"url": C, "title": "Gamma", "category": "Papers"},
}
LINK_DOC = {"links": LINKS, "sent_at": 1}


def counts(**buckets):
    return {b: {cl.url_key(u): n for u, n in m.items()} for b, m in buckets.items()}


def test_summary_totals_categories_and_top():
    s = cr.summarize("R1", LINK_DOC, counts(clicks={A: 3, B: 1, C: 2}, early={A: 5}, bots={B: 2}), sent=20)
    assert s["total"] == 6 and s["early"] == 5 and s["bots"] == 2 and s["sent"] == 20
    assert s["by_category"] == {"Industry & Business": 4, "Papers": 2}
    assert [t["title"] for t in s["top"]] == ["Alpha", "Gamma", "Beta"]


def test_ties_break_by_title_and_top_is_capped():
    links = {cl.url_key(f"https://e.com/{i}"): {"url": "x", "title": f"T{i}", "category": "A"} for i in range(5)}
    c = {"clicks": {k: 1 for k in links}}
    s = cr.summarize("R", {"links": links}, c, None)
    assert [t["title"] for t in s["top"]] == ["T0", "T1", "T2"] and s["sent"] is None


def test_counts_for_unknown_links_are_ignored():
    s = cr.summarize("R", LINK_DOC, {"clicks": {"deadbeef0000": 9, cl.url_key(A): 1}}, 10)
    assert s["total"] == 1


def test_empty_and_missing_inputs():
    s = cr.summarize("R", {}, {}, 0)
    assert s["total"] == 0 and s["top"] == [] and s["sent"] is None
    s = cr.summarize("R", LINK_DOC, {"clicks": None}, None)
    assert s["total"] == 0


def test_section_with_history():
    prior = cr.summarize("2026-10-05", LINK_DOC, counts(clicks={A: 3, C: 1}), 31)
    cur = cr.summarize("2026-10-12", LINK_DOC, counts(clicks={A: 1}), 33)
    text = cr.format_section(cur, [prior, cr.summarize("2026-09-28", LINK_DOC, counts(clicks={B: 2}), 30)])
    assert "1 click so far" in text and "last tracked week (run 2026-10-05): 4 clicks (0.13 per delivered email, 31 delivered)" in text
    assert "Industry & Business 3 (75%), Papers 1 (25%)" in text and '"Alpha" (3, Industry & Business)' in text
    assert "earlier week (run 2026-09-28)" in text and "Rough signal" in text


def test_section_without_tracking_or_history():
    text = cr.format_section(None, [])
    assert "click tracking was not on for this run" in text and "no tracked runs yet" in text


def test_zero_click_prior_week_has_no_breakdown():
    text = cr.format_section(None, [cr.summarize("R0", LINK_DOC, counts(clicks={}), 10)])
    assert "0 clicks" in text and "by category" not in text
