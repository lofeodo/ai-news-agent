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
