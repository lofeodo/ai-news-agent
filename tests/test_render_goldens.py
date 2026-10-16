"""Ready-made newsletters render byte for byte as the committed golden files (tests/fixtures/render/).

The goldens were made from compose_html before the block renderer existed, so this test is what proves the
renderer refactor changed nothing for readers. Regenerate them only for an intended email change:
python tests/make_render_goldens.py
"""
import json
import os

import pytest

import make_render_goldens as goldens

pytestmark = pytest.mark.usefixtures("_fixed_address")


@pytest.fixture
def _fixed_address(monkeypatch):
    monkeypatch.setenv("MAILING_ADDRESS", goldens.MAILING_ADDRESS)


def _load_input() -> dict:
    with open(goldens.INPUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _expected_names() -> list[str]:
    import topics
    return [f"{tid}_{key}.html" for tid in goldens.TOPIC_IDS for key in topics.get_topic(tid).variants]


def test_every_topic_and_variant_has_a_golden():
    names = _expected_names()
    assert len([n for n in names if n.startswith("ai_")]) == 4
    for name in names:
        assert os.path.isfile(os.path.join(goldens.FIXTURE_DIR, name)), name


def test_input_fixture_is_what_the_generator_builds():
    assert _load_input() == json.loads(json.dumps(goldens.build_input()))


@pytest.mark.parametrize("name", _expected_names())
def test_render_matches_golden(name):
    rendered = goldens.render_all(_load_input())[name]
    with open(os.path.join(goldens.FIXTURE_DIR, name), encoding="utf-8", newline="") as f:
        assert rendered == f.read()
