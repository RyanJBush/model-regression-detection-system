# Model Regression Detection System

A small local CLI (`mrds`) that decides whether a candidate retrieval run is a **regression** against an approved
baseline. It reads two saved run reports (`run.json`, schema 1.1) from the
[Retrieval Evaluation Harness](https://github.com/RyanJBush/retrieval-eval-harness) (pinned at commit
`5daa6003131c1ce3e11a1673c976c24dac2c80c7`), validates them, compares BM25 Recall@3, and writes a JSON and a
Markdown report. It has no runtime dependencies beyond Python 3.10+.

> **Everything in this repository is synthetic.** The baseline and the candidates are hand-written fixtures, and
> the `0.05` tolerance in policy `synthetic-v1` is a **fixture-only tolerance**. Real baseline approval and a real
> tolerance are still pending. The hosted GitHub demo below verifies CI behavior using **supplied fixture
> reports**; it does not measure any real model.

## What it measures: BM25 Recall@3

For one query, **Recall@3** is the share of its judged-relevant documents that appear in the first three ranks:

```
Recall@3(query) = (relevant document IDs within ranks 1-3) / (all judged-relevant documents for the query)
```

- It is **derived from the ranking and the judgments**, never read from the saved metric. Saved recall and the saved
  aggregate are only checked against the re-derived values.
- A query with no judged-relevant documents is **unscored**: status `no_judged_relevant`, recall `null` on both sides,
  excluded from both means. A null is never a zero.
- A query that has relevant documents and an **empty ranking scores 0** and stays in the denominator.
- **Macro recall** is the mean of Recall@3 over scored queries. The gate averages the **paired** per-query
  deltas (candidate minus baseline), paired by query ID.

All arithmetic is exact (Python `Fraction`), so candidate minus baseline, `0.60 - 0.65`, is exactly `-1/20`. In
binary floating point the same subtraction gives `-0.050000000000000044`, which is strictly below `-0.05` and would
wrongly block the boundary case.

## The three decisions

| Decision | Exit code | Meaning |
|---|---|---|
| `pass` | 0 | Mean delta is not strictly below `-tolerance`. The exact boundary passes. |
| `regression_detected` | 1 | Mean delta is strictly below `-tolerance`. |
| `comparison_invalid` | 2 | The pair cannot be compared. Rejected **before** any aggregate is computed. |

Any unexpected crash also exits 2, never 1, so a crash cannot be mistaken for a detected regression. Latency is
informational and never affects the decision.

Policy (`fixtures/synthetic-v1/policy.json`): method `bm25`, metric `macro_recall`, `k = 3`,
`absolute_tolerance = "0.05"` (a decimal **string**, parsed exactly), version `synthetic-v1`.

## Worked examples

The baseline has three queries. `q01` has 10 judged-relevant documents (`d01`-`d10`), `q02` has 1 (`d11`), and `q08`
has none.

| Query | Baseline top 3 | Relevant in top 3 | Baseline Recall@3 |
|---|---|---|---|
| q01 | d01, d02, d03 | 3 of 10 | 3/10 = 0.30 |
| q02 | d11, d12, d13 | 1 of 1 | 1 |
| q08 | d01, d02, d03 | no relevant documents | null (unscored) |

Baseline macro recall = (3/10 + 1) / 2 = **13/20 = 0.65**. `q08` is excluded.

### Why the exact -0.05 boundary passed (exit 0)

Candidate `q01` top 3 is `d01, d02, d12`. `d12` is not relevant, so q01 drops from 3/10 to **2/10**; q02 is unchanged.

- Candidate macro recall = (2/10 + 1) / 2 = 3/5 = **0.60**
- Mean delta (candidate minus baseline) = 0.60 - 0.65 = 3/5 - 13/20 = **-1/20 = -0.05**
- The rule blocks only when the delta is **strictly below** `-0.05`. `-0.05` is not below `-0.05`, so the decision is
  `pass`.

The single-query drop is larger (q01 is down 0.10), but the mean is over the two scored queries, and q02 did not move.

### Why the -0.10 regression failed (exit 1)

Candidate `q01` top 3 is `d01, d12, d13`, so q01 drops from 3/10 to **1/10**; q02 is unchanged.

- Candidate macro recall = (1/10 + 1) / 2 = 11/20 = **0.55**
- Mean delta (candidate minus baseline) = 0.55 - 0.65 = 11/20 - 13/20 = **-1/10 = -0.10**, which is strictly below `-0.05`, so the decision is
  `regression_detected`.
- Largest drop: **q01, -0.20** (3/10 to 1/10).

### Why missing q01 invalidated the third comparison (exit 2)

The invalid candidate still lists `q01` in `dataset.queries` and `dataset.judgments`, but its
`results.bm25.queries` has **no row for q01**. `q01` is a **scored** query (10 judged-relevant documents), and without
its row the candidate's Recall@3 for it is **unknown**.

Omitting it would **inflate the mean**: the candidate's saved aggregate covers `q02` alone and equals `1.0`, compared
with the baseline's 0.65. That looks like a +0.35 improvement, but it only appears because a scored query whose
candidate recall is unknown was dropped from the average. It could just as well have been a drop on `q01`, as in the
regression case. A paired delta for `q01` cannot be computed, so no honest mean exists.

Validation therefore rejects the pair with `missing_result: no result row for dataset query 'q01'` **before
aggregating**. The report has `decision: comparison_invalid`, exit 2, and no aggregate, deltas or counts.

### What validation rejects

Both reports are validated before the decision is computed. A pair is invalid if it has: a mismatched dataset
designation, dataset file SHA-256 or embedded documents/queries/judgments; a missing required file checksum
(`documents.jsonl`, `queries.jsonl`, `qrels.csv`); a query with no judgment row; a different cutoff (`ks`, `max_k`);
differing quality policies or BM25 settings (unless declared with `--declare-change FIELD=REASON`); duplicate IDs;
missing or extra query results; malformed rankings or scores; saved recall, counts or aggregate that disagree with
the re-derived values; an empty scored set; or a baseline or policy file that does not match its pinned SHA-256.

## Quickstart

Python 3.10+ and git. The commands below were run from a fresh clone of `main` (see the end of this section).

```bash
git clone https://github.com/RyanJBush/model-regression-detection-system.git
cd model-regression-detection-system
python3 -m mrds --baseline fixtures/synthetic-v1/baseline.run.json --candidate fixtures/synthetic-v1/candidate_boundary.run.json --policy fixtures/synthetic-v1/policy.json --out-dir out/boundary
python3 -m mrds --baseline fixtures/synthetic-v1/baseline.run.json --candidate fixtures/synthetic-v1/candidate_regression.run.json --policy fixtures/synthetic-v1/policy.json --out-dir out/regression
python3 -m mrds --baseline fixtures/synthetic-v1/baseline.run.json --candidate fixtures/synthetic-v1/candidate_invalid.run.json --policy fixtures/synthetic-v1/policy.json --out-dir out/invalid
```

Expected exit codes: boundary `0` (`pass`), regression `1` (`regression_detected`), invalid `2`
(`comparison_invalid`). Each run writes `report.json` and `report.md` into its `--out-dir`.

Pin the approved files by SHA-256, as CI does (a mismatch is `comparison_invalid`):

```bash
python3 -m mrds --baseline fixtures/synthetic-v1/baseline.run.json --candidate fixtures/synthetic-v1/candidate_boundary.run.json --policy fixtures/synthetic-v1/policy.json --out-dir out/pinned --baseline-sha256 e09c06922c7670e7c679f3906426c806a0aa97d57970bb3822a67fc8b4a7af43 --policy-sha256 8b0bbe70261ad29a81c1ea2b57f88c8e725b24ada25c1c3fd4fc68170c9296af
```

Run the tests (the `.venv` step avoids touching your global Python):

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install pytest
python3 -m pytest -q
```

On Windows use `python` for `python3` and `.venv\Scripts\activate` for the activation line.

Regenerate the fixtures byte-for-byte (the tests check this): `python3 tools/make_fixtures.py`.

## Hosted demo (GitHub Actions)

The workflow [`model-regression-gate`](.github/workflows/model-regression-gate.yml) runs on pull requests. Each
verification PR copies one synthetic fixture to `candidate/run.json`; the gate reads the code, baseline, policy and
pinned hashes from the **PR base commit** and treats the candidate file as data. The check is **advisory**
(`model-regression-gate` is not a required status check and `main` has no branch protection).

These runs used the hardened gate merged in [#4](https://github.com/RyanJBush/model-regression-detection-system/pull/4)
and stay **unmerged drafts**:

| Outcome | PR | Actions run | Check | Report decision / exit | Artifact |
|---|---|---|---|---|---|
| Boundary | [#5](https://github.com/RyanJBush/model-regression-detection-system/pull/5) | [42453419077](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42453419077) | pass | `pass` / 0, delta -1/20 | [13606318412](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42453419077/artifacts/13606318412) |
| Regression | [#6](https://github.com/RyanJBush/model-regression-detection-system/pull/6) | [42453423731](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42453423731) | fail | `regression_detected` / 1, delta -1/10, q01 -1/5 | [13606891062](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42453423731/artifacts/13606891062) |
| Invalid | [#7](https://github.com/RyanJBush/model-regression-detection-system/pull/7) | [42453427956](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42453427956) | fail | `comparison_invalid` / 2, q01 missing, no aggregate | [13607165078](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42453427956/artifacts/13607165078) |

Each artifact holds `report.json`, `report.md`, `console.txt`, `candidate.run.json` and a `reproduce.sh` that rebuilds
the check from a clean clone (trusted base commit plus the PR head's `candidate/run.json`). The earlier, pre-hardening
runs are PRs #1 to #3; details are in [docs/hosted-demo.md](docs/hosted-demo.md).

### What the demo does and does not prove

It verifies **hosted CI behavior using supplied fixture reports**: that GitHub shows the intended check conclusion
and publishes the job summary and artifacts for each outcome. It does **not** show that a pull request changes model
quality, because each PR just supplies a fixture as `candidate/run.json`.

**Real PR enforcement requires generating the candidate report from the exact PR commit** in a controlled evaluation,
and a **protected workflow definition** (branch protection or rulesets, plus CODEOWNERS on `.github/`, `ci/`, `mrds/`
and `fixtures/`), because a PR can otherwise edit the workflow that gates it. Until then the check stays advisory. A
PR with no `candidate/run.json` (including a docs-only PR) fails the check with `comparison_invalid`; that is the
intended fail-closed behavior.

## Reports

`report.json` and `report.md` contain the decision, reason, policy and its SHA-256, input identities (run IDs, file
SHA-256, code fingerprints), scored and unscored counts, aggregate and per-query deltas, the largest drops, any declared
setting changes, informational latency, and a reproduction command. For invalid comparisons they contain the errors
and no gate aggregate. Candidate-controlled text is rendered as inert code spans and never echoed to the CI log.

## Layout

| Path | Purpose |
|---|---|
| `mrds/` | CLI: `validate.py` (schema and pair checks), `gate.py` (exact decision), `report.py`, `mdsafe.py`, `policy.py` |
| `fixtures/synthetic-v1/` | Baseline, three candidate fixtures and the policy (synthetic) |
| `ci/` | `run_gate.sh` and `approved.env` (pinned baseline and policy hashes) |
| `.github/workflows/` | The advisory `model-regression-gate` workflow |
| `demo/` | Scripts that open the demo PRs and collect their results |
| `tools/make_fixtures.py` | Deterministic fixture generator |
| `tests/` | pytest suite |
