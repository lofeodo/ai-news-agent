"""Regenerate the README's review-eval table from the latest results file (never hand-typed).

CMD:  venv\\Scripts\\python -m evals.make_readme_table
Rewrites the block between <!-- review-eval:start --> and <!-- review-eval:end --> in README.md.
"""
import re
import sys
from pathlib import Path

from evals import results

README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- review-eval:start -->", "<!-- review-eval:end -->"

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


def main():
    path = latest_results_path()
    if path is None:
        print("no evals/results/review_eval_*.json found; run evals.run_review_eval first")
        return 1
    update_readme(results.read_results(path))
    print(f"README.md updated from {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
