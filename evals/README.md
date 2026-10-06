# evals

Shared infrastructure for the evaluation, monitoring and security steps in `docs/IMPROVEMENT_ROADMAP.md`.
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
the source text in `source_text_ref` (under the gitignored `fixtures/private/summary_sources/`). `yes` means every
factual claim (numbers, names, dates, causes, results) is stated in, or directly follows from, the source; `no`
means at least one claim is missing from the source, contradicts it or goes beyond it. Style and brevity do not
count against a summary. Use only the source; do not fill gaps from what you know. This is the same definition the
judge prompt (`prompts/judge_prompt.txt`) uses, so the two can be compared.

Once all 40 rows are labeled: `venv\Scripts\python -m evals.run_judge_calibration --dry-run`, then without
`--dry-run`. It writes `results/judge_calibration.json` (agreement, kappa with a bootstrap interval, recall and
precision for "unsupported", per-source-kind agreement) and `_rows.json` (ids, labels, verdicts; no text).
