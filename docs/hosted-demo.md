# Hosted CI demo: model-regression-gate

**Everything here is synthetic.** The baseline (`fixtures/synthetic-v1/baseline.run.json`) is a hand-written
fixture, and the `0.05` tolerance in policy `synthetic-v1` is a fixture-only tolerance. Real baseline approval
and a real tolerance are pending.

## What the demo proves, and what it does not

The three demo PRs verify **hosted CI behavior using supplied fixture reports**: that GitHub shows the intended
check conclusion, writes the job summary, and uploads `report.json` and `report.md` for each outcome.

They do **not** show that a PR's code changes model quality. Each PR just copies a fixture to
`candidate/run.json`. A required quality check for real PRs still needs:

1. a controlled evaluation that generates the candidate report from the exact PR commit, and
2. a protected workflow definition (branch protection or rulesets, plus CODEOWNERS on `.github/`, `ci/`, `mrds/`,
   `fixtures/`), because a PR can otherwise edit the workflow that gates it.

The check stays **advisory**: `model-regression-gate` is not a required status check, and branch protection is
unchanged. Any PR without a `candidate/run.json` fails the check with `comparison_invalid` (fail closed), including
PRs that only change the gate itself.

## Verified hosted runs (2026-11-08)

Repository: https://github.com/RyanJBush/model-regression-detection-system. The three PRs are drafts and stay unmerged.
For each run I checked the PR check conclusion, the `report.json` and `report.md` in the artifact, and the rendered
job summary (read from the run page while signed in).

| Outcome | PR | Actions run | Check | Report decision / exit | Artifact |
|---|---|---|---|---|---|
| Boundary | [#1](https://github.com/RyanJBush/model-regression-detection-system/pull/1) | [42451915288](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42451915288) | pass | `pass` / 0, delta -1/20 (-0.05) | [13606300489](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42451915288/artifacts/13606300489) |
| Regression | [#2](https://github.com/RyanJBush/model-regression-detection-system/pull/2) | [42451916483](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42451916483) | fail | `regression_detected` / 1, delta -1/10 (-0.10), largest drop q01 (-1/5) | [13606147602](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42451916483/artifacts/13606147602) |
| Invalid | [#3](https://github.com/RyanJBush/model-regression-detection-system/pull/3) | [42451920708](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42451920708) | fail | `comparison_invalid` / 2, q01 result row missing, no aggregate | [13605259020](https://github.com/RyanJBush/model-regression-detection-system/actions/runs/42451920708/artifacts/13605259020) |

Those runs used the gate as of commit `3b2bac9`. Two properties of that version were fixed afterwards and are not
true of the artifacts above:

- the reproduction command in their reports contains runner-absolute paths, so it does not work from a clean checkout;
- candidate-controlled text could reach the job log and the Markdown summary unescaped.

## Output hardening and reproduction (follow-up)

- **Log hygiene.** `ci/run_gate.sh` prints one fixed status line. Nothing derived from `candidate/run.json` reaches
  the job log (the runner parses `::workflow-command::` lines and may ignore leading whitespace). Full tool output is
  kept in `console.txt` in the artifact.
- **Markdown.** Candidate-controlled values (run ids, query ids, error messages, settings values, git/fingerprint
  fields, latency) are shown as code spans with control and bidi characters made visible, a fence longer than any
  backtick run, and no raw `|` in table cells. See `mrds/mdsafe.py`.
- **Reproduction.** The report's command uses repository-relative paths (`--candidate candidate/run.json`,
  `--out-dir gate-out`). When run in Actions the artifact also contains `reproduce.sh`, which clones the repository,
  checks out the trusted base commit, fetches the PR head, extracts `candidate/run.json` from that exact commit, and
  runs the same command. It refuses to run if the PR head moved.

## Re-running the demo

```bash
bash demo/create_demo_prs.sh      # preflights main, then opens 3 draft PRs; merges nothing
bash demo/verify_runs.sh          # waits for hosted runs, downloads artifacts, prints the table
```

| PR | Fixture | Expected check | Expected report |
|---|---|---|---|
| boundary | `candidate_boundary` | passes | `pass`, exit 0, delta -1/20 (-0.05) |
| regression | `candidate_regression` | fails | `regression_detected`, exit 1, delta -1/10, largest drop q01 (-1/5) |
| invalid | `candidate_invalid` | fails | `comparison_invalid`, exit 2, missing q01, no aggregate |

`demo/verify_runs.sh` cannot read job summaries (GitHub's API does not expose them); open each run's Summary tab.
