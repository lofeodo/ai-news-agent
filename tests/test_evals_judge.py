"""Calibration scoring and runner, with a stub judge. No network or keys."""
from types import SimpleNamespace

import pytest

from evals import judge_eval as je
from evals import results, run_judge_calibration as rjc


def rows(gold, kinds=None):
    kinds = kinds or ["full_text"] * len(gold)
    return [{"id": f"r{i}", "gold": g, "source_kind": k} for i, (g, k) in enumerate(zip(gold, kinds))]


def test_parse_label_variants_and_errors():
    assert [je.parse_label(x) for x in ["yes", "Y", "TRUE", "1", " supported "]] == [True] * 5
    assert [je.parse_label(x) for x in ["no", "N", "false", "0", "unsupported"]] == [False] * 5
    assert je.parse_label("") is None and je.parse_label(None) is None
    with pytest.raises(ValueError):
        je.parse_label("maybe")


def test_load_gold_parses_labels_and_blanks(tmp_path):
    f = tmp_path / "labels.csv"
    f.write_text("id,source_kind,supported
a,full_text,yes
b,full_text,No
c,description,
", encoding="utf-8")
    assert [r["gold"] for r in je.load_gold(f)] == [True, False, None]


def test_perfect_agreement():
    g = [True] * 8 + [False] * 4
    m, d = je.score(rows(g), {f"r{i}": v for i, v in enumerate(g)})
    assert m["agreement"]["value"] == 1.0 and m["cohens_kappa"]["value"] == 1.0
    assert m["unsupported_recall"]["value"] == 1.0 and m["false_alarm_rate"]["value"] == 0.0
    assert d["gold_unsupported"] == 4 and d["scored"] == 12


def test_mixed_confusion_counts_and_every_metric_has_n_and_ci():
    g = [True, True, True, True, False, False, False, False]
    j = [True, True, True, False, False, False, True, True]
    m, d = je.score(rows(g), {f"r{i}": v for i, v in enumerate(j)})
    assert d["confusion"] == {"gold_unsupported_judge_unsupported": 2, "gold_unsupported_judge_supported": 2,
                              "gold_supported_judge_unsupported": 1, "gold_supported_judge_supported": 3}
    assert m["unsupported_recall"]["value"] == 0.5 and m["unsupported_precision"]["n"] == 3
    assert abs(m["cohens_kappa"]["value"] - 0.25) < 1e-9
    for v in m.values():
        assert {"value", "n", "ci_low", "ci_high"} <= set(v)
    results.build_results("t", "m", m)  # passes the schema


def test_single_class_gold_makes_kappa_undefined_not_a_crash():
    g = [True] * 6
    m, d = je.score(rows(g), {f"r{i}": True for i in range(6)})
    assert "cohens_kappa" not in m and "kappa_note" in d and "unsupported_recall" not in m


def test_errors_and_unlabeled_rows_are_excluded_and_counted():
    rs = rows([True, False, None, True])
    m, d = je.score(rs, {"r0": True, "r1": None, "r2": True, "r3": True})
    assert d["scored"] == 2 and d["judge_errors"] == 1 and d["labeled"] == 3
    with pytest.raises(ValueError):
        je.score(rows([None]), {"r0": True})


def test_subgroup_agreement():
    g = [True, True, False, False]
    j = [True, False, False, False]
    m, _ = je.score(rows(g, ["a", "a", "b", "b"]), {f"r{i}": v for i, v in enumerate(j)})
    assert m["agreement_a"]["value"] == 0.5 and m["agreement_b"]["value"] == 1.0


def test_verdict_lines_mention_wide_interval():
    g = [True] * 8 + [False] * 4
    m, d = je.score(rows(g), {f"r{i}": v for i, v in enumerate(g)})
    text = " ".join(je.verdict_lines(m, d))
    assert "kappa 1.00" in text and "wide" in text


class StubClient:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        ok = "BAD" not in kw["messages"][0]["content"].split("<summary>")[1]
        blk = SimpleNamespace(type="tool_use", name="record_verdict",
                              input={"supported": ok, "unsupported_claims": [] if ok else ["claim"]})
        return SimpleNamespace(content=[blk], usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def test_runner_scores_stub_judge(tmp_path, monkeypatch):
    src = tmp_path / "s.txt"
    src.write_text("source text", encoding="utf-8")
    monkeypatch.setattr(rjc, "EVALS_DIR", tmp_path)
    rs = [{"id": "a", "title": "T", "kind": "news", "source_kind": "full_text", "source_text_ref": "s.txt",
           "generated_summary": "fine", "gold": True},
          {"id": "b", "title": "T", "kind": "news", "source_kind": "full_text", "source_text_ref": "s.txt",
           "generated_summary": "BAD claim", "gold": False}]
    verdicts, details, usage = rjc.run_calibration(rs, StubClient())
    assert verdicts == {"a": True, "b": False} and usage["input"] == 200
    assert details[1]["n_claims"] == 1


def test_runner_refuses_real_run_with_unlabeled_rows(capsys):
    assert rjc.main([]) == 1
    assert "no `supported` label" in capsys.readouterr().out
