# evals

Shared infrastructure for the evaluation, monitoring and security steps in `docs/history/IMPROVEMENT_ROADMAP.md`.
Every number the repo's docs quote must come from a results file written here.

## Layout

| Path | What | Tracked? |
|---|---|---|
| `stats.py` | Wilson interval, Cohen's kappa, paired wins/losses (scipy / scikit-learn wrappers) | yes |
| `cost.py` | Token-to-USD estimate and `CostGuard` (refuses runs above 2 USD without approval) | yes |
| `results.py` | Results-file schema, writer and validator | yes |
| `snapshot.py` | Builds frozen eval inputs from a real run | yes |
| `make_label_templates.py` | Generates the blank labeling CSVs | yes |
| `fixtures/` | Frozen inputs: ids, urls, titles, short snippets | yes |
| `fixtures/private/` | Full article / PDF text re-fetched by script | **no** (gitignored) |
| `labels/` | Labeling templates; filled-in label files are written by the repo owner | yes |
| `results/*.json` | Generated results | yes |

## Conventions

- **Results files** follow `results.py`: `schema_version`, `name`, `created_at`, `git_sha`, `model`, `cost_usd`, `notes`, and `metrics`.
  Each metric carries `value`, `n`, `ci_low`, `ci_high` (a Wilson 95% interval; build with `stats.rate_with_ci`).
  Compare variants with per-item paired wins and losses (`stats.paired_wins_losses`), and don't claim a difference inside the interval.
- **Models** come from `config.py`; never hardcode a model name. A model with a different price registers it via `cost.register_price`.
- **Cost**: estimate first with `cost.estimate_cost` and pass the result through `CostGuard`. Anything above 2 USD needs the owner's explicit OK.
- **Data in a public repo**: commit ids, urls, titles and short snippets only. Full text goes under `fixtures/private/` and is rebuilt by script.
- **Tests** under `tests/` use stubs and need no network or keys. Anything calling a real API is a separate on-demand command.

## Labels

Gold labels are written by hand by the repo owner; the generators never fill or edit a label column
(`gold_category`, `supported`). Regenerating a template with the same seed gives identical rows.

Known limits worth remembering when reading results:
- The articles template shows the pipeline's first-pass category next to each article, which can anchor the labeler.
- `agent1b_review_log.json` only covers articles that survived selection, so category accuracy can be measured but selection recall cannot.

## Labeling the articles template

`labels/agent1b_articles_template.csv`: fill `gold_category` with exactly one of the categories
agent1b can assign (`agents/filter_tool.py`):
Model & Product Releases, Industry & Business, Policy, Law & Regulation, Open Source & Tools,
Safety & Alignment, Society & Culture, Canada & Montreal.
`snippet` is often empty for Hacker News items (they have no description); use the title and url.
Ignore the `sample_stratum` column; it exists so the eval can report low- and high-confidence rows separately.

## Prompt-injection eval

`fixtures/injection_cases.json` holds hand-written poisoned inputs (titles, urls and short snippets only, no
third-party text). Each case names an agent, an attack type, the field the injection is appended to and a
deterministic success test (canary string, forced category, leaked guard sentence, planted fetch URL, markup
echoed by the model). `run_injection_eval.py` runs every case with the injection (attack arm) and without it
(control arm) through the real agent functions. Results: `results/injection_eval_baseline.json` (before the
Step 4 fixes) and `results/injection_eval_after.json`, with per-trial rows in the matching `_trials.json`.
Limits: no LLM judge, so subtle steering is not detected; a refusal that quotes the canary counts as a
success; n per scope is small.

## Labeling the summaries template and calibrating the judge

`labels/summaries_template.csv`: fill `supported` with `yes` or `no` for each generated summary, judged against
the source text in `source_text_ref` (under the gitignored `fixtures/private/summary_sources/`). Judge against that
file, not the live link (a dead link does not matter if the file has the text).

The summarizer prompts ask for two things: say what the source reports, and add a short significance sentence
("why it matters", "the single most important implication", "what comes next"). Label the two differently,
the same way the judge prompt (`prompts/judge_prompt.txt`) does:

- **Factual claims** (what happened, who, numbers, dates, findings, causes stated as fact) must be stated in the
  source or follow directly from it, in one short mechanical step. A fact, number or name that is missing,
  contradicted or invented makes the row `no`. Do not fill gaps from what you know, even when it is true.
- **Significance sentences** are fine if they are a reasonable reading of the source and add no new specific
  fact, figure, name, event or outcome. Generic framing is fine. Mark `no` if one adds such a specific, contradicts
  the source, or overstates it ("proves", "will replace", "first ever" when the source does not say so).

Style and brevity never count against a summary. If the source file is empty or only an error page, leave the
row blank (it will be dropped and n reported accordingly). A `used_fallback` row is judged against the short
description it was written from, so be strict there. Keep a side list of ids you could not decide on.

Once all 40 rows are labeled: `venv\Scripts\python -m evals.run_judge_calibration --dry-run`, then without
`--dry-run`. It writes `results/judge_calibration.json` (agreement, kappa with a bootstrap interval, recall and
precision for "unsupported", per-source-kind agreement) and `_rows.json` (ids, labels, verdicts; no text).

## Dedup eval dataset (roadmap Step 5)

`python -m evals.make_dedup_cases` freezes cases of ~8 articles each (`fixtures/dedup_cases.json`, titles,
300-char snippets and summaries only) and writes a blank `labels/dedup_cases_template.csv`. Case kinds: `real`
(a suspected duplicate pair found by word overlap), `hard_negative` (same topic, probably different event),
`control` (no suspected pair) and `synthetic` (hand-written rewrites listed in `fixtures/dedup_synthetic.json` as
`{source_url, title, summary, language?}`; the builder adds each next to its real source article).

Labeling rule, same as `prompts/dedup_prompt.txt`: the same specific story is a duplicate, even across outlets
and languages and even with a different focus or opinion (price vs. benchmarks of one release; a report vs. a critical
take); the same company or broad topic alone is not. In `duplicate_group` put the same label (`g1`, `g2`, ...) on
articles that are duplicates of each other; leave unique ones blank. Put `?` in `note` for an ambiguous article
(excluded from the metrics) and `t` for same topic but a different angle (borderline: kept out of the strict gold and
reported separately). Gold labels are written by hand only. `evals.dedup_labels.load_dedup_gold` validates
the file and lists cases not yet touched.
