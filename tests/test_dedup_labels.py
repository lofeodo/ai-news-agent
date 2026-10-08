import json

import pytest

from evals.dedup_labels import LabelError, load_dedup_gold

HEADER = "case_id,article_id,title,snippet,kind,duplicate_group,note\n"


def setup(tmp_path, rows):
    cases = {"cases": [{"case_id": "c1", "articles": [{"id": "a"}, {"id": "b"}, {"id": "c"}]},
                       {"case_id": "c2", "articles": [{"id": "d"}]}]}
    cp = tmp_path / "cases.json"
    cp.write_text(json.dumps(cases), encoding="utf-8")
    lp = tmp_path / "labels.csv"
    lp.write_text(HEADER + "".join(f"{c},{a},t,s,real,{g},{n}\n" for c, a, g, n in rows), encoding="utf-8")
    return lp, cp


def test_groups_and_unlabeled(tmp_path):
    lp, cp = setup(tmp_path, [("c1", "a", "g1", ""), ("c1", "b", "g1", ""), ("c1", "c", "", ""), ("c2", "d", "", "")])
    gold = load_dedup_gold(lp, cp)
    assert gold.groups == {"c1": [frozenset({"a", "b"})]}
    assert gold.unlabeled_cases == ["c2"]


def test_question_mark_excluded(tmp_path):
    lp, cp = setup(tmp_path, [("c1", "a", "g1", ""), ("c1", "b", "g1", ""), ("c1", "c", "", "?")])
    gold = load_dedup_gold(lp, cp)
    assert gold.excluded == {"c1": {"c"}}


@pytest.mark.parametrize("rows", [
    [("zz", "a", "g1", "")],                       # unknown case
    [("c1", "zz", "g1", "")],                      # unknown article
    [("c1", "a", "g1", ""), ("c1", "b", "", "")],  # one-member group
])
def test_bad_labels_rejected(tmp_path, rows):
    lp, cp = setup(tmp_path, rows)
    with pytest.raises(LabelError):
        load_dedup_gold(lp, cp)


def test_reads_excel_bom_and_accents(tmp_path):
    lp, cp = setup(tmp_path, [("c1", "a", "g1", ""), ("c1", "b", "g1", "")])
    lp.write_text(lp.read_text(encoding="utf-8").replace(",t,", ",été,"), encoding="utf-8-sig")
    assert load_dedup_gold(lp, cp).groups == {"c1": [frozenset({"a", "b"})]}


def test_restores_leading_zero_dropped_by_excel(tmp_path):
    cases = {"cases": [{"case_id": "c1", "articles": [{"id": "051952554936"}, {"id": "000000000abc"}]}]}
    cp = tmp_path / "cases.json"
    cp.write_text(json.dumps(cases), encoding="utf-8")
    lp = tmp_path / "labels.csv"
    rows = ["c1,51952554936,t,s,real,g1,", "c1,000000000abc,t,s,real,g1,"]
    lp.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    assert load_dedup_gold(lp, cp).groups == {"c1": [frozenset({"051952554936", "000000000abc"})]}


def test_load_all_merges_sets_and_rejects_collisions(tmp_path):
    lp, cp = setup(tmp_path, [("c1", "a", "g1", ""), ("c1", "b", "g1", "")])
    (tmp_path / "x").mkdir()
    lp2, cp2 = setup(tmp_path / "x", [("c1", "a", "g1", ""), ("c1", "b", "g1", "")])
    from evals.dedup_labels import load_all_gold
    with pytest.raises(LabelError):
        load_all_gold([(lp, cp), (lp2, cp2)])
    assert load_all_gold([(lp, cp)]).groups == {"c1": [frozenset({"a", "b"})]}
