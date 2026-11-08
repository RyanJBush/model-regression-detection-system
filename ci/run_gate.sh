#!/usr/bin/env bash
# CI entry point. Runs from the TRUSTED checkout (the PR base commit):
#   bash trusted/ci/run_gate.sh <candidate run.json> <out dir>
# The gate code, approved baseline, policy and pinned hashes all come from this checkout. The candidate file is
# the only input from the pull request, and it is treated as data.
#
# Exit codes: 0 pass, 1 regression_detected, 2 comparison_invalid or any failure to get a verified decision.
set -u

if [ "$#" -ne 2 ]; then
  echo "usage: run_gate.sh CANDIDATE_RUN_JSON OUT_DIR" >&2
  exit 2
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${PYTHON:-python3}"
cand_in="$1"
mkdir -p "$2" || exit 2
out="$(cd "$2" && pwd)"

summary() {  # append stdin to the job summary when running in Actions
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat >> "$GITHUB_STEP_SUMMARY"; else cat > /dev/null; fi
}
fail() {
  echo "run_gate: $1" >&2
  printf '## model-regression-gate: comparison_invalid\n\n%s\n' "$1" | summary
  exit 2
}

cfg() { sed -n "s/^$1=//p" "$root/ci/approved.env" | head -n 1 | tr -d '\r'; }
baseline="$(cfg BASELINE)"; baseline_sha="$(cfg BASELINE_SHA256)"
policy="$(cfg POLICY)"; policy_sha="$(cfg POLICY_SHA256)"
for name in baseline baseline_sha policy policy_sha; do
  [ -n "${!name}" ] || fail "ci/approved.env in the trusted revision is missing $name"
done

# Candidate bytes are copied once into the output dir (also kept as evidence). Symlinks are not followed.
cand="$out/candidate.run.json"
rm -f "$cand"
if [ -f "$cand_in" ] && [ ! -L "$cand_in" ]; then
  cp -- "$cand_in" "$cand" || fail "cannot copy candidate run file"
else
  echo "run_gate: candidate is missing or not a regular file: $cand_in" >&2
fi

( cd "$root" && "$py" -B -m mrds \
    --baseline "$baseline" --baseline-sha256 "$baseline_sha" \
    --policy "$policy" --policy-sha256 "$policy_sha" \
    --candidate "$cand" --out-dir "$out" ) > "$out/console.txt" 2>&1
code=$?

# Candidate-derived text may reach the console; neutralise lines that would be read as workflow commands.
sed 's/^::/ ::/' "$out/console.txt"

# Only trust an exit code the tool itself recorded in its report. An interpreter crash exits 1 and must not
# be mistaken for a detected regression.
rep_code="$(sed -n 's/^  "exit_code": \([0-9]*\),\{0,1\}$/\1/p' "$out/report.json" 2>/dev/null | head -n 1)"
if [ "$code" -gt 2 ] || [ "$rep_code" != "$code" ]; then
  echo "run_gate: exit code $code does not match report exit_code '${rep_code}'; treating as comparison_invalid" >&2
  code=2
fi

if [ -f "$out/report.md" ]; then
  summary < "$out/report.md"
else
  printf '## model-regression-gate: comparison_invalid\n\nNo report was produced.\n' | summary
fi
exit "$code"
