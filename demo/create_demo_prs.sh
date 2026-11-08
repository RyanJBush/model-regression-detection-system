#!/usr/bin/env bash
# Creates three DRAFT demo PRs against main, one per synthetic fixture. Each PR adds only candidate/run.json.
# Run from a clean clone AFTER main contains the bootstrap commit (see docs/hosted-demo.md).
# Does not merge anything and does not touch branch protection.
set -euo pipefail
export MSYS2_ARG_CONV_EXCL='*'   # Git Bash on Windows would otherwise rewrite "origin/main:path" arguments
base="${BASE:-main}"

git fetch -q origin "$base"
for f in .github/workflows/model-regression-gate.yml ci/run_gate.sh ci/approved.env mrds/__main__.py \
         fixtures/synthetic-v1/baseline.run.json fixtures/synthetic-v1/policy.json; do
  git cat-file -e "origin/$base:$f" 2>/dev/null || { echo "origin/$base lacks $f; bootstrap main first" >&2; exit 2; }
done
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 2; }

declare -A FIXTURE=([boundary]=candidate_boundary [regression]=candidate_regression [invalid]=candidate_invalid)
declare -A EXPECT=(
  [boundary]="pass, exit 0, mean delta exactly -0.05 (check should PASS)"
  [regression]="regression_detected, exit 1, mean delta -0.10, largest drop q01 (check should FAIL)"
  [invalid]="comparison_invalid, exit 2, q01 result row missing, no aggregate (check should FAIL)"
)

for s in boundary regression invalid; do
  br="demo/synthetic-$s"
  git switch -q --detach "origin/$base"
  git switch -q -c "$br"
  mkdir -p candidate
  git show "origin/$base:fixtures/synthetic-v1/${FIXTURE[$s]}.run.json" > candidate/run.json
  git add candidate/run.json
  git commit -q -m "Demo ($s): supply synthetic fixture as candidate/run.json" \
    -m "Synthetic fixture only; not a real evaluation of this commit."
  git push -q -u origin "$br"
  gh pr create --draft --base "$base" --head "$br" \
    --title "Demo: synthetic $s candidate (do not merge)" \
    --body "Hosted-CI demo using the supplied synthetic fixture \`${FIXTURE[$s]}.run.json\`. Baseline and the 0.05 tolerance are synthetic.

Expected: ${EXPECT[$s]}.

This PR only adds \`candidate/run.json\`. It must stay unmerged."
done
git switch -q --detach "origin/$base"
gh pr list --state open --search "Demo: synthetic" --json number,url,headRefName,isDraft
