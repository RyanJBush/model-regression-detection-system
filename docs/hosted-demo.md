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

The check stays **advisory** for the demo: do not add `model-regression-gate` to branch protection.

## Prerequisite: main must contain the gate

The workflow reads the gate code, pinned baseline and policy from the **PR base commit**, so they must already be
on `main` before any demo PR opens. State when this was written: the local repo had no remote and no commits
(branch `master`), so nothing was on GitHub.

Bootstrap (creates a repository; choose name and visibility yourself):

```bash
git branch -M main
git commit -m "Add mrds CLI, synthetic fixtures, tests and model-regression-gate workflow"
gh repo create <owner>/model-regression-detection-system --private --source . --remote origin --push
```

Check the Actions tab is enabled. Do not enable branch protection.

## Create the three draft PRs

```bash
bash demo/create_demo_prs.sh      # preflights main, then opens 3 draft PRs; merges nothing
bash demo/verify_runs.sh          # waits for hosted runs, downloads artifacts, prints the table
```

| PR | Fixture | Expected check | Expected report |
|---|---|---|---|
| boundary | `candidate_boundary` | passes | `pass`, exit 0, delta -1/20 (-0.05) |
| regression | `candidate_regression` | fails | `regression_detected`, exit 1, delta -1/10, largest drop q01 (-1/5) |
| invalid | `candidate_invalid` | fails | `comparison_invalid`, exit 2, missing q01, no aggregate |

Leave all three PRs unmerged.
