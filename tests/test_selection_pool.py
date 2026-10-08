"""Ranked runners-up pool from agent3's article selection (no network)."""
import re

import agent3_compose as a3
import config
from test_injection_prompts import TextClient

CAT3 = "Safety & Alignment"      # cap 3
CAT4 = "Model & Product Releases"  # cap 4


def articles(n, hn=None):
    return [{"title": f"t{i}", "url": f"https://e.com/{i}", "summary": f"s{i}",
             **({"hn_score": hn[i]} if hn and hn[i] is not None else {})} for i in range(n)]


def run(reply, n=10, category=CAT3, hn=None):
    client = TextClient(reply)
    res = a3.select_with_runners_up(category, articles(n, hn), "{category}{articles}", client)
    return res, client


def shown(client):
    return dict(re.findall(r"^\[(\d+)\] (t\d+)$", client.calls[0]["messages"][0]["content"], re.M))


def titles(arts):
    return [a["title"] for a in arts]


def test_parse_object_reply():
    assert a3.parse_selection('{"selected": [1, 2], "runners_up": [5, 3]}', 10) == ([1, 2], [5, 3])


def test_parse_bare_array_has_no_runners_up():
    assert a3.parse_selection("[1, 2, 3]", 10) == ([1, 2, 3], [])


def test_parse_drops_bad_runner_ups():
    sel, run_ = a3.parse_selection('{"selected": [1, 2], "runners_up": [2, 4, 4, 99, true, "x", 5]}', 10)
    assert sel == [1, 2] and run_ == [4, 5]


def test_parse_garbage_and_fenced():
    assert a3.parse_selection("nope", 10) == ([], [])
    assert a3.parse_selection('```json\n{"selected": [1], "runners_up": [2]}\n```', 10) == ([1], [2])


def test_model_runners_up_come_first_in_order():
    res, c = run('{"selected": [0, 1], "runners_up": [7, 4]}')
    s = shown(c)
    assert titles(res.picks) == [s["0"], s["1"]]
    assert titles(res.runners_up)[:2] == [s["7"], s["4"]]


def test_pool_is_topped_up_to_max_and_disjoint_from_picks():
    res, _ = run("[0, 1]", n=12)
    assert len(res.runners_up) == config.RUNNERS_UP_MAX
    assert not {a["url"] for a in res.picks} & {a["url"] for a in res.runners_up}


def test_top_up_orders_by_hn_then_shuffled_position():
    hn = [None, 50, 900, None, 10, 300, None, None]
    res, c = run("[0]", n=8, hn=hn)
    got = res.runners_up
    scored = [a["hn_score"] for a in got if "hn_score" in a]
    assert scored == sorted(scored, reverse=True)
    assert all("hn_score" in a for a in got[:len(scored)])   # scored ones before unscored


def test_top_up_is_reproducible():
    r1, _ = run("[0]")
    r2, _ = run("[0]")
    assert titles(r1.runners_up) == titles(r2.runners_up)


def test_pool_limited_by_article_count():
    res, _ = run("[0, 1]", n=4)
    assert len(res.picks) == 2 and len(res.runners_up) == 2


def test_cap_trimmed_picks_lead_the_pool():
    res, c = run('{"selected": [0, 1, 2, 3, 4], "runners_up": [8]}', category=CAT3)
    s = shown(c)
    assert len(res.picks) == 3
    assert titles(res.runners_up)[:3] == [s["3"], s["4"], s["8"]]


def test_four_cap_section():
    res, _ = run("[0, 1, 2, 3, 4, 5]", category=CAT4)
    assert len(res.picks) == 4


def test_empty_reply_fallback_keeps_pool_disjoint():
    res, _ = run("garbage", n=8)
    assert len(res.picks) == 3
    assert not {a["url"] for a in res.picks} & {a["url"] for a in res.runners_up}


def test_no_articles():
    res = a3.select_with_runners_up(CAT3, [], "{articles}", TextClient("[]"))
    assert res.picks == [] and res.runners_up == []


def test_wrapper_returns_same_picks():
    arts = articles(10)
    picks = a3.select_articles_for_category(CAT3, arts, "{category}{articles}", TextClient("[2, 3]"))
    res = a3.select_with_runners_up(CAT3, arts, "{category}{articles}", TextClient("[2, 3]"))
    assert picks == res.picks


def test_real_prompt_renders_with_runners_up_count():
    from pathlib import Path
    text = (Path(a3.__file__).resolve().parent.parent / "prompts" / "article_selection_prompt.txt").read_text(encoding="utf-8")
    out = text.format(category=CAT3, articles="X", cap=3, runners_up=6)
    assert "up to 6 runners-up" in out and '"runners_up"' in out


def test_take_fallback_fills_gap_only():
    pool = articles(6)
    kept = pool[:3]
    assert a3.take_fallback(CAT3, kept, pool[3:]) is None            # at cap, nothing to fill
    assert a3.take_fallback(CAT3, kept[:2], pool[3:]) is pool[3]     # one gap
    assert a3.take_fallback(CAT4, kept, pool[3:]) is pool[3]         # cap 4 has room


def test_take_fallback_skips_used_and_kept():
    pool = articles(6)
    kept = [pool[0]]
    assert a3.take_fallback(CAT3, kept, [pool[0], pool[1], pool[2]], used=[pool[1]]) is pool[2]
    assert a3.take_fallback(CAT3, kept, [pool[0], pool[1]], used=[pool[1]]) is None
