import pytest

from evals import review_eval as re_


def _row(i, gold, single, first, conf, final=None, stratum="low_confidence", routed=False, tools=0, tin=0, tout=0,
         status="skipped"):
    return {"id": i, "stratum": stratum, "gold": gold, "single_pass": single, "first_pass": first,
            "confidence": conf, "final": final or first, "routed": routed, "tool_calls": tools,
            "input_tokens": tin, "output_tokens": tout, "review_status": status}


def test_load_gold_skips_blank_labels(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("id,url,sample_stratum,gold_category\na,http://u/a,low_confidence,Policy\nb,http://u/b,high_confidence,\n",
                 encoding="utf-8")
    assert re_.load_gold(p) == {"a": {"gold": "Policy", "stratum": "low_confidence", "url": "http://u/a"}}


def test_join_keeps_only_predicted_gold_rows():
    gold = {"a": {"gold": "X", "stratum": "s"}, "b": {"gold": "Y", "stratum": "s"}}
    rows = re_.join(gold, {"a": {"first_pass": "X"}, "zzz": {"first_pass": "Q"}})
    assert rows == [{"id": "a", "stratum": "s", "gold": "X", "first_pass": "X"}]


def test_accuracy_overall_and_per_stratum():
    rows = [_row("1", "A", "A", "A", 5, stratum="high_confidence"),
            _row("2", "A", "B", "B", 3),
            _row("3", "A", "A", "B", 3, final="A")]
    m = re_.accuracy_metrics(rows)
    assert m["accuracy_single_pass"]["value"] == pytest.approx(2 / 3)
    assert m["accuracy_graph_first_pass"]["value"] == pytest.approx(1 / 3)
    assert m["accuracy_graph_final"]["value"] == pytest.approx(2 / 3)
    assert m["accuracy_graph_final__low_confidence"]["n"] == 2
    assert m["accuracy_graph_final__high_confidence"]["value"] == 1.0


def test_calibration_buckets_and_spearman():
    rows = [_row(str(i), "A", "A", "A", 5) for i in range(4)] + [_row(str(i + 9), "A", "A", "B", 2) for i in range(4)]
    m = re_.calibration_metrics(rows)
    assert m["calibration_first_pass__conf_5"]["value"] == 1.0
    assert m["calibration_first_pass__conf_2"]["value"] == 0.0
    assert m["confidence_correctness_spearman"]["value"] == pytest.approx(1.0)
    assert "calibration_first_pass__conf_3" not in m


def test_spearman_is_none_when_confidence_constant():
    rows = [_row("1", "A", "A", "A", 4), _row("2", "A", "A", "B", 4)]
    assert re_.calibration_metrics(rows)["confidence_correctness_spearman"]["value"] is None


def test_verdict_predictive_when_intervals_separate():
    rows = [_row(str(i), "A", "A", "A", 5) for i in range(60)] + [_row(str(i + 99), "A", "A", "B", 2) for i in range(60)]
    assert "predicts errors" in re_.confidence_verdict(rows, 4)


def test_verdict_admits_noise():
    rows = ([_row(str(i), "A", "A", "A", 5) for i in range(5)] + [_row("x", "A", "A", "B", 5)]
            + [_row(str(i + 9), "A", "A", "A", 2) for i in range(4)] + [_row("y", "A", "A", "B", 2)])
    assert "does NOT clearly predict" in re_.confidence_verdict(rows, 4)


def test_verdict_handles_empty_side():
    assert "Cannot assess" in re_.confidence_verdict([_row("1", "A", "A", "A", 5)], 4)


def test_routing_metrics():
    rows = [_row("1", "A", "A", "B", 2, final="A", routed=True, tools=2, tin=100, tout=10, status="ok"),
            _row("2", "A", "A", "B", 2, routed=True, tools=1, tin=300, tout=30, status="review_failed"),
            _row("3", "A", "A", "A", 5), _row("4", "A", "A", "A", 5)]
    m = re_.routing_metrics(rows)
    assert m["share_routed_to_review"]["value"] == 0.5 and m["share_routed_to_review"]["n"] == 4
    assert m["mean_tool_calls_per_reviewed"]["value"] == 1.5
    assert m["review_input_tokens_per_reviewed"]["value"] == 200
    assert m["review_degraded_share"]["value"] == 0.5


def test_routing_metrics_without_review():
    assert set(re_.routing_metrics([_row("1", "A", "A", "A", 5)])) == {"share_routed_to_review"}


def test_paired_counts_wins_and_losses():
    rows = [_row("1", "A", "B", "B", 2, final="A"),   # graph fixes single_pass
            _row("2", "A", "A", "A", 2, final="B"),   # review breaks it
            _row("3", "A", "A", "A", 5),              # tie
            _row("4", "A", "B", "B", 2, final="A")]
    m = re_.paired_metrics(rows)["paired_graph_final_vs_single_pass"]
    assert (m["wins"], m["losses"], m["ties"], m["items"]) == (2, 1, 1, 4)
    assert m["n"] == 3 and m["value"] == pytest.approx(2 / 3)
    effect = re_.paired_metrics(rows)["paired_review_effect"]
    assert (effect["wins"], effect["losses"]) == (2, 1)


def test_score_output_passes_results_schema():
    from evals import results
    rows = [_row("1", "A", "A", "B", 2, final="A", routed=True, tools=1, tin=10, tout=1, status="ok"),
            _row("2", "A", "A", "A", 5, stratum="high_confidence")]
    metrics, verdict = re_.score(rows, 4)
    doc = results.build_results("t", "m", metrics, notes=verdict)
    assert doc["notes"] == verdict
