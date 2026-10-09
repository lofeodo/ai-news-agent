"""Generate the README's result charts (pure-Python SVG, no plotting dependency) from evals/results/*.json.

Every number drawn comes from a results file; nothing is typed here. Each chart is a self-contained dark card (like
the banner) so it reads the same whatever theme the viewer's GitHub uses.

CMD:  venv\\Scripts\\python -m evals.make_charts      (also run by evals.make_readme_table)
"""
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from evals import results

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets"

KAPPA_BAR = 0.6   # the working bar set for the judge in the roadmap; below it the judge is not trusted

BG, CARD_LINE, GRID = "#161616", "#2a2a2a", "#2a2a2a"
TEXT, MUTED, SAND = "#e8e8e8", "#8b8b8b", "#c8b89a"
BEFORE, AFTER, CONTROL = "#e5484d", "#3fb950", "#8b8b8b"
FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"


def _svg(width, height, body, title, desc):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'role="img" aria-label="{escape(title)}. {escape(desc)}">\n'
            f'<title>{escape(title)}</title><desc>{escape(desc)}</desc>\n'
            f'<style>text{{font-family:{FONT};fill:{TEXT}}} .m{{fill:{MUTED}}} .s{{fill:{SAND}}}</style>\n'
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="14" fill="{BG}" stroke="{CARD_LINE}"/>\n'
            f'{body}\n</svg>\n')


def _pct(v):
    return f"{v * 100:.0f}%"


def _overlap(a, b):
    return a["ci_low"] <= b["ci_high"] and b["ci_low"] <= a["ci_high"]


# ---------------------------------------------------------------- review accuracy
_REVIEW_BARS = (("Single-pass", "accuracy_single_pass"),
                ("Graph, first pass", "accuracy_graph_first_pass"),
                ("Graph, after review", "accuracy_graph_final"))


def review_accuracy_svg(doc):
    m = doc["metrics"]
    bars = [(label, m[key]) for label, key in _REVIEW_BARS if key in m]
    if not bars:
        raise ValueError("review eval has none of the accuracy metrics")
    n = bars[0][1]["n"]
    W, H = 880, 340
    x0, x1, y0, y1 = 90, 840, 84, 270       # plot box; y0 = 100%, y1 = 0%
    ys = lambda v: y1 - (y1 - y0) * v
    parts = [f'<text x="28" y="40" font-size="20" font-weight="600" class="s">Category accuracy by variant</text>',
             f'<text x="28" y="62" font-size="13" class="m">Category accuracy on {n} hand-labeled articles, with Wilson 95% intervals</text>']
    for v in (0, .25, .5, .75, 1):
        parts.append(f'<line x1="{x0}" x2="{x1}" y1="{ys(v):.1f}" y2="{ys(v):.1f}" stroke="{GRID}"/>'
                     f'<text x="{x0 - 10}" y="{ys(v) + 4:.1f}" font-size="12" text-anchor="end" class="m">{_pct(v)}</text>')
    slot = (x1 - x0) / len(bars)
    colors = ("#6b7280", "#4f46e5", "#7c3aed")
    for i, (label, d) in enumerate(bars):
        cx = x0 + slot * (i + .5)
        w = 110
        top = ys(d["value"])
        parts.append(f'<rect x="{cx - w / 2:.1f}" y="{top:.1f}" width="{w}" height="{y1 - top:.1f}" rx="6" fill="{colors[i % 3]}"/>')
        parts.append(f'<text x="{cx:.1f}" y="{(top + y1) / 2 + 8:.1f}" font-size="22" font-weight="700" text-anchor="middle">{_pct(d["value"])}</text>')
        lo, hi = ys(d["ci_low"]), ys(d["ci_high"])
        parts.append(f'<g stroke="{TEXT}" stroke-width="2"><line x1="{cx:.1f}" x2="{cx:.1f}" y1="{lo:.1f}" y2="{hi:.1f}"/>'
                     f'<line x1="{cx - 9:.1f}" x2="{cx + 9:.1f}" y1="{lo:.1f}" y2="{lo:.1f}"/>'
                     f'<line x1="{cx - 9:.1f}" x2="{cx + 9:.1f}" y1="{hi:.1f}" y2="{hi:.1f}"/></g>')
        parts.append(f'<text x="{cx:.1f}" y="{y1 + 22}" font-size="14" text-anchor="middle">{escape(label)}</text>')
    all_overlap = all(_overlap(a[1], b[1]) for a in bars for b in bars)
    note = ("All three intervals overlap: no measurable difference" if all_overlap
            else "Some intervals do not overlap: see the paired comparison in docs/evaluation.md")
    parts.append(f'<text x="{W - 28}" y="40" font-size="13" text-anchor="end" class="m">{note}</text>')
    desc = "; ".join(f"{label} {_pct(d['value'])} ({_pct(d['ci_low'])} to {_pct(d['ci_high'])})" for label, d in bars)
    return _svg(W, H, "\n".join(parts), "Review loop accuracy", f"n={n}. {desc}")


# ---------------------------------------------------------------- prompt injection
def _injection_scopes(before):
    """Overall plus every agent/attack scope where an attack succeeded before the fixes (the rest are all zero)."""
    scopes = ["overall"]
    for key, d in before.items():
        if key.startswith("attack_success__") and key != "attack_success__overall" and d["k"] > 0:
            scopes.append(key.split("__", 1)[1])
    return scopes


def _scope_label(scope):
    return "All attacks" if scope == "overall" else scope.replace("agent__", "agent: ").replace("attack__", "attack: ").replace("_", " ")


def injection_svg(baseline, after):
    b, a = baseline["metrics"], after["metrics"]
    scopes = _injection_scopes(b)
    row_h = 66
    W, H = 880, 146 + row_h * len(scopes)
    x0, x1 = 250, 830                      # 0..axis_max maps onto this range
    highs = [d["ci_high"] for src in (b, a) for k, d in src.items() if k.endswith(tuple(scopes)) and "ci_high" in d]
    axis_max = min(1.0, max(0.2, math.ceil(max(highs) / 0.2) * 0.2))
    xs = lambda v: x0 + (x1 - x0) * v / axis_max
    ticks = [round(i * 0.2, 1) for i in range(int(round(axis_max / 0.2)) + 1)]
    parts = [f'<text x="28" y="40" font-size="20" font-weight="600" class="s">Attack success rate</text>',
             f'<text x="28" y="62" font-size="13" class="m">Share of trials where the attack achieved its goal, with Wilson 95% intervals</text>']
    for i, (label, color) in enumerate((("Before fixes", BEFORE), ("After fixes", AFTER), ("Control (no injection)", CONTROL))):
        lx = 28 + i * 150
        parts.append(f'<rect x="{lx}" y="78" width="12" height="12" rx="3" fill="{color}"/>'
                     f'<text x="{lx + 18}" y="89" font-size="12">{label}</text>')
    top = 108
    for v in ticks:
        parts.append(f'<line x1="{xs(v):.1f}" x2="{xs(v):.1f}" y1="{top}" y2="{top + row_h * len(scopes)}" stroke="{GRID}"/>'
                     f'<text x="{xs(v):.1f}" y="{top + row_h * len(scopes) + 18}" font-size="12" text-anchor="middle" class="m">{_pct(v)}</text>')
    for r, scope in enumerate(scopes):
        y = top + r * row_h
        parts.append(f'<text x="{x0 - 14}" y="{y + row_h / 2 + 4:.1f}" font-size="14" text-anchor="end">{escape(_scope_label(scope))}</text>')
        series = ((b.get(f"attack_success__{scope}"), BEFORE), (a.get(f"attack_success__{scope}"), AFTER),
                  (a.get(f"control_success__{scope}"), CONTROL))
        for j, (d, color) in enumerate(series):
            if not d:
                continue
            by = y + 9 + j * 16
            width = max(xs(d["value"]) - x0, 2)
            parts.append(f'<rect x="{x0}" y="{by}" width="{width:.1f}" height="11" rx="3" fill="{color}"/>')
            parts.append(f'<g stroke="{TEXT}" stroke-width="1.5"><line x1="{xs(d["ci_low"]):.1f}" x2="{xs(d["ci_high"]):.1f}" '
                         f'y1="{by + 5.5}" y2="{by + 5.5}"/><line x1="{xs(d["ci_high"]):.1f}" x2="{xs(d["ci_high"]):.1f}" '
                         f'y1="{by + 1}" y2="{by + 10}"/></g>')
            parts.append(f'<text x="{xs(d["ci_high"]) + 8:.1f}" y="{by + 10}" font-size="11" class="m">{d["k"]}/{d["n"]}</text>')
    ov_b, ov_a = b["attack_success__overall"], a["attack_success__overall"]
    desc = f"Overall {ov_b['k']}/{ov_b['n']} attacks succeeded before the fixes and {ov_a['k']}/{ov_a['n']} after."
    return _svg(W, H, "\n".join(parts), "Prompt-injection attack success", desc)


# ---------------------------------------------------------------- judge kappa
def judge_kappa_svg(doc):
    m = doc["metrics"]["cohens_kappa"]
    W, H = 880, 210
    lo_axis, hi_axis = -0.2, 1.0
    x0, x1 = 60, 820
    xs = lambda v: x0 + (x1 - x0) * (v - lo_axis) / (hi_axis - lo_axis)
    y = 130
    parts = [f'<text x="28" y="40" font-size="20" font-weight="600" class="s">Judge agreement with human labels</text>',
             f'<text x="28" y="62" font-size="13" class="m">Cohen\'s kappa against {m["n"]} hand-labeled summaries (bootstrap 95% interval)</text>',
             f'<rect x="{xs(lo_axis)}" y="{y - 7}" width="{xs(KAPPA_BAR) - xs(lo_axis):.1f}" height="14" rx="7" fill="{BEFORE}" fill-opacity=".22"/>',
             f'<rect x="{xs(KAPPA_BAR):.1f}" y="{y - 7}" width="{xs(hi_axis) - xs(KAPPA_BAR):.1f}" height="14" rx="7" fill="{AFTER}" fill-opacity=".22"/>']
    for v in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        parts.append(f'<text x="{xs(v):.1f}" y="{y + 34}" font-size="12" text-anchor="middle" class="m">{v:.1f}</text>')
    parts.append(f'<line x1="{xs(0):.1f}" x2="{xs(0):.1f}" y1="{y - 24}" y2="{y + 14}" stroke="{MUTED}" stroke-dasharray="3 3"/>'
                 f'<text x="{xs(0):.1f}" y="{y - 30}" font-size="12" text-anchor="middle" class="m">chance</text>')
    parts.append(f'<line x1="{xs(KAPPA_BAR):.1f}" x2="{xs(KAPPA_BAR):.1f}" y1="{y - 24}" y2="{y + 14}" stroke="{AFTER}" stroke-width="2"/>'
                 f'<text x="{xs(KAPPA_BAR):.1f}" y="{y - 30}" font-size="12" text-anchor="middle" fill="{AFTER}" style="fill:{AFTER}">working bar {KAPPA_BAR:.1f}</text>')
    parts.append(f'<g stroke="{TEXT}" stroke-width="2"><line x1="{xs(m["ci_low"]):.1f}" x2="{xs(m["ci_high"]):.1f}" y1="{y}" y2="{y}"/>'
                 f'<line x1="{xs(m["ci_low"]):.1f}" x2="{xs(m["ci_low"]):.1f}" y1="{y - 8}" y2="{y + 8}"/>'
                 f'<line x1="{xs(m["ci_high"]):.1f}" x2="{xs(m["ci_high"]):.1f}" y1="{y - 8}" y2="{y + 8}"/></g>'
                 f'<circle cx="{xs(m["value"]):.1f}" cy="{y}" r="9" fill="{SAND}" stroke="{BG}" stroke-width="2"/>')
    parts.append(f'<text x="{xs(m["value"]):.1f}" y="{y + 62}" font-size="15" font-weight="700" text-anchor="middle" class="s">'
                 f'kappa {m["value"]:.2f} ({m["ci_low"]:.2f} to {m["ci_high"]:.2f}): report-only</text>')
    desc = f"Cohen's kappa {m['value']:.2f}, interval {m['ci_low']:.2f} to {m['ci_high']:.2f}, against a working bar of {KAPPA_BAR}."
    return _svg(W, H, "\n".join(parts), "Summary judge calibration", desc)


# ---------------------------------------------------------------- duplicate removal
_DEDUP_ARMS = (("Control", "control", "#6b7280"), ("Haiku", "haiku", "#4f46e5"), ("Sonnet", "sonnet", "#7c3aed"))
_DEDUP_PANELS = (("Duplicate recall", "duplicate_recall", "higher is better"),
                 ("Duplicates left in the section", "residual_duplicate_rate", "lower is better"))


def dedup_svg(doc):
    m = doc["metrics"]
    W, H = 880, 350
    y0, y1 = 124, 280                       # y0 = 100%, y1 = 0%
    ys = lambda v: y1 - (y1 - y0) * v
    parts = ['<text x="28" y="40" font-size="20" font-weight="600" class="s">Duplicate removal by arm</text>',
             '<text x="28" y="62" font-size="13" class="m">Control removes nothing. Hand-labeled article sets, Wilson 95% intervals</text>']
    desc = []
    for p, (title, key, hint) in enumerate(_DEDUP_PANELS):
        px0, px1 = (90, 420) if p == 0 else (500, 840)
        parts.append(f'<text x="{px0}" y="88" font-size="14" font-weight="600">{escape(title)} <tspan class="m" font-weight="400">({hint})</tspan></text>')
        for v in (0, .5, 1):
            parts.append(f'<line x1="{px0}" x2="{px1}" y1="{ys(v):.1f}" y2="{ys(v):.1f}" stroke="{GRID}"/>'
                         f'<text x="{px0 - 8}" y="{ys(v) + 4:.1f}" font-size="11" text-anchor="end" class="m">{_pct(v)}</text>')
        bars = [(label, m[f"{key}__{arm}"], color) for label, arm, color in _DEDUP_ARMS if f"{key}__{arm}" in m]
        slot = (px1 - px0) / max(len(bars), 1)
        for i, (label, d, color) in enumerate(bars):
            if d.get("value") is None:
                continue
            cx, w, top = px0 + slot * (i + .5), 64, ys(d["value"])
            parts.append(f'<rect x="{cx - w / 2:.1f}" y="{top:.1f}" width="{w}" height="{max(y1 - top, 1):.1f}" rx="5" fill="{color}"/>')
            lo, hi = ys(d["ci_low"]), ys(d["ci_high"])
            parts.append(f'<g stroke="{TEXT}" stroke-width="2"><line x1="{cx:.1f}" x2="{cx:.1f}" y1="{lo:.1f}" y2="{hi:.1f}"/>'
                         f'<line x1="{cx - 8:.1f}" x2="{cx + 8:.1f}" y1="{lo:.1f}" y2="{lo:.1f}"/>'
                         f'<line x1="{cx - 8:.1f}" x2="{cx + 8:.1f}" y1="{hi:.1f}" y2="{hi:.1f}"/></g>')
            parts.append(f'<text x="{cx:.1f}" y="{min(hi, top) - 8:.1f}" font-size="14" font-weight="700" text-anchor="middle">{_pct(d["value"])}</text>')
            parts.append(f'<text x="{cx:.1f}" y="{y1 + 20}" font-size="13" text-anchor="middle">{label}</text>')
            desc.append(f"{title}, {label} {_pct(d['value'])}")
    pair = [m.get(f"duplicate_recall__{a}") for a in ("haiku", "sonnet")]
    if all(pair):
        note = ("Haiku and Sonnet intervals overlap: no measurable difference" if _overlap(*pair)
                else "Haiku and Sonnet intervals do not overlap: see docs/evaluation.md")
        parts.append(f'<text x="{W - 28}" y="40" font-size="13" text-anchor="end" class="m">{note}</text>')
    return _svg(W, H, "\n".join(parts), "Duplicate removal", "; ".join(desc))


# ---------------------------------------------------------------- driver
def write_all(results_dir=results.RESULTS_DIR, out_dir=ASSETS):
    """Write every chart whose results file exists; returns the list of files written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def emit(name, svg):
        (out_dir / name).write_text(svg, encoding="utf-8")
        written.append(out_dir / name)

    reviews = sorted(p for p in Path(results_dir).glob("review_eval_*.json") if not p.stem.endswith("_rows"))
    if reviews:
        emit("chart-review-accuracy.svg", review_accuracy_svg(results.read_results(reviews[-1])))
    base, after = (Path(results_dir) / f"injection_eval_{n}.json" for n in ("baseline", "after"))
    if base.exists() and after.exists():
        emit("chart-injection.svg", injection_svg(results.read_results(base), results.read_results(after)))
    dedups = sorted(p for p in Path(results_dir).glob("dedup_eval_*.json") if not p.stem.endswith("_rows"))
    if dedups:
        emit("chart-dedup.svg", dedup_svg(results.read_results(dedups[-1])))
    judge = Path(results_dir) / "judge_calibration.json"
    if judge.exists():
        emit("chart-judge-kappa.svg", judge_kappa_svg(results.read_results(judge)))
    return written


def main():
    for p in write_all():
        print(f"wrote {p.relative_to(ASSETS.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
