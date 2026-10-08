"""Hard per-section caps on agent3's article selection (no network)."""
import re
from pathlib import Path

import pytest

import agent3_compose as a3
import config
from test_injection_prompts import TextClient

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "article_selection_prompt.txt"
FOUR_CAP = ["Model & Product Releases", "Open Source & Tools"]
THREE_CAP = [c for c in a3.NEWS_CATEGORIES if c not in FOUR_CAP]


def articles(n):
    return [{"title": f"t{i}", "url": f"https://e.com/{i}", "summary": f"s{i}"} for i in range(n)]


def select(category, reply, n=8, template="{category}{articles}{cap}"):
    return a3.select_articles_for_category(category, articles(n), template, TextClient(reply))


@pytest.mark.parametrize("category", FOUR_CAP)
def test_four_cap_sections_keep_four(category):
    assert len(select(category, "[0, 1, 2, 3, 4, 5]")) == 4


@pytest.mark.parametrize("category", THREE_CAP)
def test_other_sections_keep_three(category):
    assert len(select(category, "[0, 1, 2, 3, 4, 5]")) == 3


def test_truncation_keeps_the_models_first_picks():
    client = TextClient("[5, 2, 7, 1, 0]")
    out = a3.select_articles_for_category(THREE_CAP[0], articles(8), "{articles}", client)
    # indices refer to the shuffled list shown in the prompt; map index -> title from it
    shown = dict(re.findall(r"^\[(\d+)\] (t\d+)$", client.calls[0]["messages"][0]["content"], re.M))
    assert [a["title"] for a in out] == [shown["5"], shown["2"], shown["7"]]


def test_under_cap_is_untouched():
    assert len(select(THREE_CAP[0], "[1, 2]")) == 2


def test_repeated_indices_are_collapsed():
    out = select(THREE_CAP[0], "[2, 2, 5, 2]")
    assert len(out) == 2 and len({a["url"] for a in out}) == 2


def test_repeats_do_not_eat_the_cap():
    assert len(select(THREE_CAP[0], "[1, 1, 1, 2, 3, 4]")) == 3


def test_out_of_range_indices_ignored_before_capping():
    assert len(select(THREE_CAP[0], "[99, 0, 1, 2, 3]", n=5)) == 3


@pytest.mark.parametrize("category,cap", [(FOUR_CAP[0], 4), (THREE_CAP[0], 3)])
def test_empty_reply_fallback_respects_cap(category, cap):
    assert len(select(category, "not json")) == cap


def test_fallback_is_limited_by_pool_size():
    assert len(select(FOUR_CAP[0], "not json", n=2)) == 2


def test_section_caps_for_all_categories():
    caps = {c: config.section_cap(c) for c in a3.NEWS_CATEGORIES}
    assert caps == {
        "Model & Product Releases": 4,
        "Policy, Law & Regulation": 3,
        "Open Source & Tools": 4,
        "Safety & Alignment": 3,
        "Industry & Business": 3,
        "Society & Culture": 3,
        "Canada & Montreal": 3,
    }


def test_unknown_category_gets_default_cap():
    assert config.section_cap("Something New") == config.SECTION_CAP_DEFAULT


@pytest.mark.parametrize("category,cap", [(FOUR_CAP[0], 4), (THREE_CAP[0], 3)])
def test_real_prompt_states_the_cap(category, cap):
    client = TextClient("[0]")
    a3.select_articles_for_category(category, articles(3), PROMPT.read_text(encoding="utf-8"), client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert f"at most {cap}" in prompt
    assert "{cap}" not in prompt
