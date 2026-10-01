import pytest

from evals.results import SCHEMA_VERSION, build_results, read_results, validate, write_results
from evals.stats import rate_with_ci


def _doc():
    return build_results("demo", "some-model", {"accuracy": rate_with_ci(8, 10)}, cost_usd=0.1, notes="x")


def test_round_trip(tmp_path):
    doc = _doc()
    path = write_results(doc, tmp_path)
    assert path.name == "demo.json"
    assert read_results(path) == doc
    assert doc["schema_version"] == SCHEMA_VERSION


def test_rejects_metric_without_n_or_ci():
    doc = _doc()
    del doc["metrics"]["accuracy"]["n"]
    with pytest.raises(ValueError):
        validate(doc)
    doc = _doc()
    del doc["metrics"]["accuracy"]["ci_low"]
    with pytest.raises(ValueError):
        validate(doc)


def test_rejects_missing_top_level_and_empty_metrics():
    doc = _doc()
    del doc["git_sha"]
    with pytest.raises(ValueError):
        validate(doc)
    with pytest.raises(ValueError):
        build_results("demo", "m", {})
