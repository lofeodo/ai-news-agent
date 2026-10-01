"""Results-file schema: every number in the docs must come from one of these files."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
RESULTS_DIR = Path(__file__).parent / "results"

_REQUIRED_TOP = ("schema_version", "name", "created_at", "git_sha", "model", "metrics")
_REQUIRED_METRIC = ("value", "n", "ci_low", "ci_high")


def _git_sha():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def validate(doc):
    """Raise ValueError if `doc` doesn't follow the schema. Every metric must carry n and a CI."""
    for key in _REQUIRED_TOP:
        if key not in doc:
            raise ValueError(f"results missing top-level field {key!r}")
    if doc["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {doc['schema_version']!r}")
    if not isinstance(doc["metrics"], dict) or not doc["metrics"]:
        raise ValueError("results need at least one metric")
    for name, metric in doc["metrics"].items():
        for key in _REQUIRED_METRIC:
            if key not in metric:
                raise ValueError(f"metric {name!r} missing {key!r}")
        if not isinstance(metric["n"], int) or metric["n"] < 0:
            raise ValueError(f"metric {name!r} has invalid n")
    return doc


def build_results(name, model, metrics, cost_usd=None, notes=None):
    """`metrics`: {metric_name: {value, n, ci_low, ci_high, ...}} (see stats.rate_with_ci)."""
    return validate({
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "model": model,
        "cost_usd": cost_usd,
        "notes": notes or "",
        "metrics": metrics,
    })


def write_results(doc, directory=RESULTS_DIR):
    validate(doc)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{doc['name']}.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def read_results(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))
