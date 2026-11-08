#!/usr/bin/env bash
# CI entry point. Runs from the TRUSTED checkout (the PR base commit):
#   bash trusted/ci/run_gate.sh <candidate run.json> <out dir>
# The gate code, approved baseline, policy and pinned hashes all come from this checkout. The candidate file is
# the only input from the pull request, and it is treated as data.
#
# Optional environment (GitHub context values, not candidate-controlled): MRDS_BASE_SHA, MRDS_HEAD_SHA,
# MRDS_PR_NUMBER, GITHUB_SERVER_URL, GITHUB_REPOSITORY. When all are present and well formed, a reproduce.sh
# that rebuilds this check from a clean clone is written next to the report.
#
# Console policy: nothing derived from the candidate is echoed to this script's stdout/stderr, because the runner
# parses workflow commands ("::name::") out of the log and may ignore leading whitespace. The full tool output is
# kept in out/console.txt (an artifact) and the report is published as the job summary.
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
fail() {  # $1 must be a fixed message, never candidate-derived text
  echo "run_gate: $1" >&2
  printf '## model-regression-gate: comparison_invalid\n\n%s\n' "$1" | summary
  exit 2
}

cfg() { sed -n "s/^$1=//p" "$root/ci/approved.env" | head -n 1 | tr -d '\r'; }
baseline="$(cfg BASELINE)"; baseline_sha="$(cfg BASELINE_SHA256)"
policy="$(cfg POLICY)"; policy_sha="$(cfg POLICY_SHA256)"
for name in baseline baseline_sha policy policy_sha; do
  [ -n "${!name}" ] || fail "ci/approved.env in the trusted revision is incomplete"
done

# Candidate bytes are copied once into the output dir (also kept as evidence). Symlinks are not followed.
cand="$out/candidate.run.json"
rm -f "$cand"
if [ -f "$cand_in" ] && [ ! -L "$cand_in" ]; then
  cp -- "$cand_in" "$cand" || fail "cannot copy the candidate run file"
else
  echo "run_gate: candidate is missing or not a regular file" >&2
fi

# The reproduction command is shown with portable, repository-relative paths (see reproduce.sh below).
( cd "$root" && "$py" -B -m mrds \
    --baseline "$baseline" --baseline-sha256 "$baseline_sha" \
    --policy "$policy" --policy-sha256 "$policy_sha" \
    --candidate "$cand" --repro-candidate candidate/run.json \
    --out-dir "$out" --repro-out-dir gate-out ) > "$out/console.txt" 2>&1
code=$?

# Only trust an exit code the tool itself recorded in its report. An interpreter crash exits 1 and must not
# be mistaken for a detected regression.
rep_code="$(sed -n 's/^  "exit_code": \([0-9]*\),\{0,1\}$/\1/p' "$out/report.json" 2>/dev/null | head -n 1)"
if [ "$code" -gt 2 ] || [ "$rep_code" != "$code" ]; then
  echo "run_gate: exit code does not match the report; treating as comparison_invalid" >&2
  code=2
fi

decision="$(sed -n 's/^  "decision": "\([a-z_]*\)",$/\1/p' "$out/report.json" 2>/dev/null | head -n 1)"
case "$decision" in pass|regression_detected|comparison_invalid) ;; *) decision=unknown ;; esac
echo "model-regression-gate: decision=$decision exit_code=$code (details: job summary and the model-regression-report artifact)"

# reproduce.sh: clean-clone recipe built only from GitHub context values and the tool's own command string.
sha40='^[0-9a-f]{40}$'
if [[ "${MRDS_BASE_SHA:-}" =~ $sha40 && "${MRDS_HEAD_SHA:-}" =~ $sha40 && "${MRDS_PR_NUMBER:-}" =~ ^[0-9]+$ \
      && "${GITHUB_SERVER_URL:-}" =~ ^https://[A-Za-z0-9.-]+$ && "${GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  cmd="$("$py" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["reproduction_command"])' \
         "$out/report.json" 2>/dev/null)"
  if [ -n "$cmd" ]; then
    {
      echo '#!/usr/bin/env bash'
      echo '# Rebuilds this check from a clean clone: trusted revision (PR base) + the PR head candidate file.'
      echo '# Synthetic baseline and fixture-only 0.05 tolerance. Needs git and Python 3.10+. Exit code = gate decision.'
      echo 'set -euo pipefail'
      echo "git clone --quiet ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}.git mrds-repro"
      echo 'cd mrds-repro'
      echo "git checkout --quiet ${MRDS_BASE_SHA}"
      echo "git fetch --quiet origin pull/${MRDS_PR_NUMBER}/head"
      echo "[ \"\$(git rev-parse FETCH_HEAD)\" = \"${MRDS_HEAD_SHA}\" ] || { echo 'PR head moved since this run' >&2; exit 2; }"
      echo 'mkdir -p candidate'
      echo "git show ${MRDS_HEAD_SHA}:candidate/run.json > candidate/run.json"
      echo "${cmd/#python /python3 }"
    } > "$out/reproduce.sh"
  fi
fi

if [ -f "$out/report.md" ]; then
  summary < "$out/report.md"
else
  printf '## model-regression-gate: comparison_invalid\n\nNo report was produced.\n' | summary
fi
exit "$code"
