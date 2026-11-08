#!/usr/bin/env bash
# For each demo PR: wait for the hosted run on the PR head commit, then print check conclusion, run URL,
# artifact URL, and decision/exit_code read from the downloaded report.json. Read-only.
set -euo pipefail
tmp="$(mktemp -d)"
repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
printf '%-11s %-6s %-48s %-12s %-20s %-4s %s\n' scenario pr run conclusion decision exit artifact
for s in boundary regression invalid; do
  br="demo/synthetic-$s"
  pr="$(gh pr list --head "$br" --state open --json number -q '.[0].number')"
  sha="$(gh pr view "$pr" --json headRefOid -q .headRefOid)"
  run=""
  for _ in $(seq 1 60); do   # up to ~10 minutes
    run="$(gh run list --workflow model-regression-gate.yml --branch "$br" --event pull_request \
           --json databaseId,headSha,status -q "[.[] | select(.headSha==\"$sha\")][0] | select(.status==\"completed\") | .databaseId" || true)"
    [ -n "$run" ] && break
    sleep 10
  done
  [ -n "$run" ] || { echo "$s: run for $sha did not complete in time" >&2; continue; }
  conclusion="$(gh run view "$run" --json conclusion -q .conclusion)"
  art="$(gh api "repos/$repo/actions/runs/$run/artifacts" -q '.artifacts[] | select(.name=="model-regression-report") | .id')"
  gh run download "$run" -n model-regression-report -D "$tmp/$s" >/dev/null
  decision="$(sed -n 's/^  "decision": "\(.*\)",$/\1/p' "$tmp/$s/report.json" | head -n1)"
  code="$(sed -n 's/^  "exit_code": \([0-9]*\),$/\1/p' "$tmp/$s/report.json" | head -n1)"
  printf '%-11s %-6s %-48s %-12s %-20s %-4s %s\n' "$s" "#$pr" "https://github.com/$repo/actions/runs/$run" \
    "$conclusion" "$decision" "$code" "https://github.com/$repo/actions/runs/$run/artifacts/$art"
  ls "$tmp/$s" | tr '\n' ' '; echo
done
echo "downloaded reports: $tmp"
echo "Job summaries are not exposed by the REST API: open each run page and read the 'Summary' tab."
