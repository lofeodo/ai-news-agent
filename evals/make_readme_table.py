"""Regenerate the README's eval tables from the results files (never hand-typed).

CMD:  venv\\Scripts\\python -m evals.make_readme_table
Rewrites the block between <!-- review-eval:start --> and <!-- review-eval:end --> (latest review eval) and the
block between <!-- injection-eval:start --> and <!-- injection-eval:end --> (injection_eval_baseline vs
injection_eval_after) in docs/evaluation.md.
It also fills the generic metric tables in GENERIC_BLOCKS (judge calibration and the drift simulations), each
between <!-- NAME:start --> and <!-- NAME:end --> markers.
"""
import re
import sys
from pathlib import Path

from evals import results

README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- review-eval:start -->", "<!-- review-eval:end -->"
INJ_START, INJ_END = "<!-- injection-eval:start -->", "<!-- injection-eval:end -->"

# (marker name, results file stem, show the file's notes?): one generic "metric | value | n" table per file.
# The simulation notes are long and describe assumptions, which the README states in its own prose instead.
GENERIC_BLOCKS = (
    ("judge-calibration", "judge_calibration", True),
    ("judge-drift-sim", "judge_drift_simulation", False),
    ("drift-null-sim", "drift_null_simulation", False),
    ("usage-drift-sim", "usage_drift_simulation", False),
)

# (label, metric key). Anything missing from the results file is skipped.
_ACCURACY = (
    ("Single-pass", "accuracy_single_pass"),
    ("Graph, first pass", "accuracy_graph_first_pass"),
    ("Graph, after review", "accuracy_graph_final"),
)


def latest_results_path(directory=results.RESULTS_DIR):
    paths = sorted(Path(directory).glob("review_eval_*.json"))
    paths = [p for p in paths if not p.stem.endswith("_rows")]
    return paths[-1] if paths else None


def _pct(m):
    if m["value"] is None:
        return "n/a"
    ci = f" ({m['ci_low']:.0%}–{m['ci_high']:.0%})" if m.get("ci_low") is not None else ""
    return f"{m['value']:.0%}{ci}"


def render(doc):
    metrics = doc["metrics"]
    lines = [f"Generated from `evals/results/{doc['name']}.json` (git `{doc['git_sha']}`, "
             f"{doc['created_at'][:10]}, cost ${doc['cost_usd']}). Wilson 95% intervals in parentheses.", "",
             "| Variant | All labeled | Low-confidence stratum | High-confidence stratum |", "|---|---|---|---|"]
    for label, key in _ACCURACY:
        if key not in metrics:
            continue
        cells = [_pct(metrics[key])]
        for stratum in ("low_confidence", "high_confidence"):
            m = metrics.get(f"{key}__{stratum}")
            cells.append(f"{_pct(m)}, n={m['n']}" if m else "n/a")
        lines.append(f"| {label} | {cells[0]}, n={metrics[key]['n']} | {cells[1]} | {cells[2]} |")

    lines.append("")
    for key, label in (("paired_graph_final_vs_single_pass", "Graph final vs single-pass"),
                       ("paired_review_effect", "Review vs graph first pass")):
        m = metrics.get(key)
        if m:
            lines.append(f"- **{label}** (paired, per article): {m['wins']} wins, {m['losses']} losses, "
                         f"{m['ties']} ties out of {m['items']}.")
    routed = metrics.get("share_routed_to_review")
    if routed:
        lines.append(f"- **Routed to review:** {_pct(routed)} of {routed['n']} labeled articles.")
    tools = metrics.get("mean_tool_calls_per_reviewed")
    if tools:
        lines.append(f"- **Mean tool calls per reviewed article:** {tools['value']:.2f} (n={tools['n']}).")
    if doc.get("notes"):
        lines += ["", f"> {doc['notes']}"]
    return "\n".join(lines)


def update_readme(doc, readme=README):
    """Replace the marked block; raises if the markers are missing so a typo can't silently drop the table."""
    text = Path(readme).read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise ValueError(f"{readme} has no {START} ... {END} block")
    block = f"{START}\n{render(doc)}\n{END}"
    Path(readme).write_text(pattern.sub(lambda _: block, text, count=1), encoding="utf-8")


def _inj_cell(m):
    if not m:
        return "n/a"
    return f"{m['k']}/{m['n']} ({m['ci_low']:.0%}–{m['ci_high']:.0%})"


def render_injection(baseline, after):
    """Before/after attack-success table. Control = the same inputs with the injection removed."""
    b, a = baseline["metrics"], after["metrics"]
    lines = [f"Generated from `evals/results/{baseline['name']}.json` (git `{baseline['git_sha']}`, "
             f"cost ${baseline['cost_usd']}) and `evals/results/{after['name']}.json` (git `{after['git_sha']}`, "
             f"cost ${after['cost_usd']}). Cells are attacks that achieved their goal out of trials, with Wilson 95% "
             "intervals; the control column is the same inputs without the injection, after the fixes.", "",
             "| Scope | Before fixes | After fixes | Control (after) |", "|---|---|---|---|"]
    scopes = (["overall"]
              + sorted(k.split("__", 1)[1] for k in a if k.startswith("attack_success__agent__"))
              + sorted(k.split("__", 1)[1] for k in a if k.startswith("attack_success__attack__")))
    for scope in scopes:
        label = scope.replace("agent__", "agent: ").replace("attack__", "attack: ")
        lines.append(f"| {label} | {_inj_cell(b.get('attack_success__' + scope))} | "
                     f"{_inj_cell(a.get('attack_success__' + scope))} | {_inj_cell(a.get('control_success__' + scope))} |")
    asked_after = a.get("fetch_attempted__attack")
    if asked_after:
        # The baseline harness counted every URL the model asked for, and production had no fetch guard then,
        # so its ssrf_steer rate is also its "asked for" rate.
        lines += ["", "Planted-URL fetches: before the fixes the model's request went straight to the network "
                  f"({_inj_cell(b.get('attack_success__attack__ssrf_steer'))}). After the fixes the model still asked "
                  f"for the planted URL in {_inj_cell(asked_after)} of trials; the fetch guard blocked those that "
                  f"were internal addresses (reached the network: {_inj_cell(a.get('attack_success__attack__ssrf_steer'))})."]
    notes = [after["notes"]] if after.get("notes") else []
    return "\n".join(lines + [""] + [f"> {n}" for n in notes])


def update_readme_injection(baseline, after, readme=README):
    text = Path(readme).read_text(encoding="utf-8")
    pattern = re.compile(re.escape(INJ_START) + r".*?" + re.escape(INJ_END), re.S)
    if not pattern.search(text):
        raise ValueError(f"{readme} has no {INJ_START} ... {INJ_END} block")
    block = f"{INJ_START}\n{render_injection(baseline, after)}\n{INJ_END}"
    Path(readme).write_text(pattern.sub(lambda _: block, text, count=1), encoding="utf-8")


def render_generic(doc, notes=True, keys=None):
    """Every metric of a results file as a row; values print as percentages except kappa, a plain number."""
    lines = [f"Generated from `evals/results/{doc['name']}.json` (git `{doc['git_sha']}`, "
             f"{doc['created_at'][:10]}, cost ${doc['cost_usd']}). 95% intervals in parentheses.", "",
             "| Metric | Value | n |", "|---|---|---|"]
    for key, m in doc["metrics"].items():
        if keys is not None and key not in keys:
            continue
        if key == "cohens_kappa":
            ci = f" ({m['ci_low']:.2f} to {m['ci_high']:.2f})" if m.get("ci_low") is not None else ""
            value = f"{m['value']:.2f}{ci}"
        else:
            value = _pct(m)
        lines.append(f"| {key.replace('_', ' ')} | {value} | {m['n']} |")
    if notes and doc.get("notes"):
        lines += ["", f"> {doc['notes']}"]
    return "\n".join(lines)


def update_readme_generic(name, doc, readme=README, notes=True, keys=None):
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    text = Path(readme).read_text(encoding="utf-8")
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise ValueError(f"{readme} has no {start} ... {end} block")
    block = f"{start}\n{render_generic(doc, notes, keys)}\n{end}"
    Path(readme).write_text(pattern.sub(lambda _: block, text, count=1), encoding="utf-8")


DEDUP_START, DEDUP_END = "<!-- dedup-eval:start -->", "<!-- dedup-eval:end -->"
_DEDUP_ARMS = (("Control (removes nothing)", "control"), ("Haiku", "haiku"), ("Sonnet", "sonnet"))
_DEDUP_COLUMNS = (("Duplicate recall", "duplicate_recall"), ("False removal", "false_removal_rate_strict"),
                  ("False groups", "false_group_rate_strict"), ("Residual duplicates", "residual_duplicate_rate"),
                  ("Exact case match", "case_exact_match"), ("Failed runs", "failure_rate"))


def latest_dedup_path(directory=results.RESULTS_DIR):
    paths = [p for p in sorted(Path(directory).glob("dedup_eval_*.json")) if not p.stem.endswith("_rows")]
    return paths[-1] if paths else None


def render_dedup(doc):
    m = doc["metrics"]
    lines = [f"Generated from `evals/results/{doc['name']}.json` (git `{doc['git_sha']}`, "
             f"{doc['created_at'][:10]}, cost ${doc['cost_usd']}). Wilson 95% intervals in parentheses.", "",
             "| Arm | " + " | ".join(c for c, _ in _DEDUP_COLUMNS) + " | Mean seconds per case |",
             "|---|" + "---|" * (len(_DEDUP_COLUMNS) + 1)]
    for label, arm in _DEDUP_ARMS:
        if f"case_exact_match__{arm}" not in m:
            continue
        cells = [_pct(m[f"{key}__{arm}"]) if f"{key}__{arm}" in m else "n/a" for _, key in _DEDUP_COLUMNS]
        secs = m.get(f"seconds_mean__{arm}")
        cells.append(f"{secs['value']:.1f}" if secs else "n/a")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    for key, label in (("paired_recall_sonnet_vs_haiku", "Duplicate recall, Sonnet vs Haiku"),
                       ("paired_false_removal_sonnet_vs_haiku", "False removal, Sonnet vs Haiku")):
        p = m.get(key)
        if p:
            lines.append(f"- **{label}** (paired, per item): {p['wins']} wins, {p['losses']} losses, "
                         f"{p['ties']} ties out of {p['items']}.")
    if doc.get("notes"):
        lines += ["", f"> {doc['notes']}"]
    return "\n".join(lines)


def update_readme_dedup(doc, readme=README):
    text = Path(readme).read_text(encoding="utf-8")
    pattern = re.compile(re.escape(DEDUP_START) + r".*?" + re.escape(DEDUP_END), re.S)
    if not pattern.search(text):
        raise ValueError(f"{readme} has no {DEDUP_START} ... {DEDUP_END} block")
    block = f"{DEDUP_START}\n{render_dedup(doc)}\n{DEDUP_END}"
    Path(readme).write_text(pattern.sub(lambda _: block, text, count=1), encoding="utf-8")


ROOT = README.parent
DOCS = ROOT / "docs"


def main():
    """Full tables go to docs/; the README shows the charts that evals.make_charts writes from the same results."""
    from evals import make_charts

    path = latest_results_path()
    if path is None:
        print("no evals/results/review_eval_*.json found; run evals.run_review_eval first")
        return 1
    update_readme(results.read_results(path), DOCS / "evaluation.md")
    print(f"docs/evaluation.md updated from {path.name}")
    base, after = (results.RESULTS_DIR / f"injection_eval_{n}.json" for n in ("baseline", "after"))
    if base.exists() and after.exists():
        update_readme_injection(results.read_results(base), results.read_results(after), DOCS / "evaluation.md")
        print("docs/evaluation.md updated from injection_eval_baseline.json and injection_eval_after.json")
    dedup = latest_dedup_path()
    if dedup is not None:
        update_readme_dedup(results.read_results(dedup), DOCS / "evaluation.md")
        print(f"docs/evaluation.md updated from {dedup.name}")
    for name, stem, notes in GENERIC_BLOCKS:
        path = results.RESULTS_DIR / f"{stem}.json"
        if path.exists():
            update_readme_generic(name, results.read_results(path), DOCS / "monitoring.md", notes=notes)
            print(f"docs/monitoring.md updated from {path.name}")
    for chart in make_charts.write_all():
        print(f"wrote {chart.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
