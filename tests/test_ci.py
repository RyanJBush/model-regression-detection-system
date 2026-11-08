"""CI wiring: pinned SHA-256 flags, ci/run_gate.sh for the three outcomes, and static checks of the workflow."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIX, ROOT, codes, load
from mrds.cli import main

SHA = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
BASE = FIX / "baseline.run.json"
POLICY = FIX / "policy.json"


def _bash():
    candidates = [os.environ.get("MRDS_TEST_BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")]
    for c in candidates:
        if c and Path(c).exists() and "system32" not in c.lower():
            return c
    return None


needs_bash = pytest.mark.skipif(_bash() is None, reason="bash not available")


def approved():
    env = {}
    for line in (ROOT / "ci" / "approved.env").read_text().splitlines():
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k] = v
    return env


# ----------------------------------------------------------------------------- pinned hashes in the CLI
def run_pinned(tmp_path, cand, **pins):
    args = ["--baseline", str(BASE), "--candidate", str(FIX / cand), "--policy", str(POLICY),
            "--out-dir", str(tmp_path / "o")]
    for flag, value in pins.items():
        args += [f"--{flag.replace('_', '-')}", value]
    code = main(args)
    import json
    return code, json.loads((tmp_path / "o" / "report.json").read_text())


def test_matching_pins_are_recorded_and_pass(tmp_path):
    code, rep = run_pinned(tmp_path, "candidate_boundary.run.json", baseline_sha256=SHA(BASE),
                           policy_sha256=SHA(POLICY))
    assert code == 0
    b = rep["inputs"]["baseline"]
    assert b["pinned_sha256"] == SHA(BASE) and b["sha256_verified"] is True
    assert "--baseline-sha256" in rep["reproduction_command"]
    assert "verified" in (tmp_path / "o" / "report.md").read_text()


def test_baseline_hash_mismatch_is_comparison_invalid_before_aggregation(tmp_path):
    code, rep = run_pinned(tmp_path, "candidate_boundary.run.json", baseline_sha256="0" * 64)
    assert code == 2 and rep["decision"] == "comparison_invalid" and rep["aggregate"] is None
    assert "baseline_hash_mismatch" in codes(rep)
    assert rep["inputs"]["baseline"]["sha256_verified"] is False


def test_policy_hash_mismatch_is_comparison_invalid(tmp_path):
    code, rep = run_pinned(tmp_path, "candidate_regression.run.json", policy_sha256="f" * 64)
    assert code == 2 and rep["aggregate"] is None and "policy_hash_mismatch" in codes(rep)


def test_malformed_pin_is_a_usage_error():
    with pytest.raises(SystemExit) as e:
        main(["--baseline", "a", "--candidate", "b", "--policy", "c", "--out-dir", "d", "--baseline-sha256", "XYZ"])
    assert e.value.code == 2


def test_approved_env_pins_match_the_committed_files():
    env = approved()
    assert SHA(ROOT / env["BASELINE"]) == env["BASELINE_SHA256"]
    assert SHA(ROOT / env["POLICY"]) == env["POLICY_SHA256"]
    assert "synthetic" in (ROOT / "ci" / "approved.env").read_text().lower()  # fixture label stays on the pins
    assert "fixture-only" in POLICY.read_text()


# ----------------------------------------------------------------------------- ci/run_gate.sh
def gate(tmp_path, candidate, root=ROOT, name="o"):
    summary = tmp_path / f"{name}-summary.md"
    env = {**os.environ, "PYTHON": sys.executable, "GITHUB_STEP_SUMMARY": str(summary)}
    out = tmp_path / name
    r = subprocess.run([_bash(), (root / "ci" / "run_gate.sh").as_posix(), Path(candidate).as_posix(),
                        out.as_posix()], capture_output=True, text=True, env=env, cwd=tmp_path)
    return r, out, (summary.read_text(encoding="utf-8") if summary.exists() else "")


@needs_bash
@pytest.mark.parametrize("cand,exit_code,decision", [
    ("candidate_boundary", 0, "pass"),
    ("candidate_regression", 1, "regression_detected"),
    ("candidate_invalid", 2, "comparison_invalid"),
])
def test_ci_script_maps_all_three_outcomes_and_publishes_reports(tmp_path, cand, exit_code, decision):
    r, out, summary = gate(tmp_path, FIX / f"{cand}.run.json")
    assert r.returncode == exit_code, r.stdout + r.stderr
    assert (out / "report.json").is_file() and (out / "report.md").is_file()
    assert f'"decision": "{decision}"' in (out / "report.json").read_text()
    assert decision in summary and "synthetic" in summary.lower() and "fixture-only tolerance" in summary
    assert "verified" in summary  # baseline pin


def _copy_trusted(tmp_path):
    t = tmp_path / "trusted"
    for d in ("ci", "mrds", "fixtures"):
        shutil.copytree(ROOT / d, t / d, ignore=shutil.ignore_patterns("__pycache__"))
    return t


@needs_bash
def test_tampered_baseline_fails_hash_check_with_report(tmp_path):
    t = _copy_trusted(tmp_path)
    b = t / "fixtures" / "synthetic-v1" / "baseline.run.json"
    b.write_text(b.read_text(encoding="utf-8") + " ", encoding="utf-8", newline="\n")  # still valid JSON, new hash
    r, out, summary = gate(tmp_path, FIX / "candidate_boundary.run.json", root=t)
    assert r.returncode == 2
    assert "baseline_hash_mismatch" in (out / "report.json").read_text()
    assert '"aggregate": null' in (out / "report.json").read_text()


@needs_bash
def test_interpreter_crash_exits_2_not_1(tmp_path):
    t = _copy_trusted(tmp_path)
    (t / "mrds" / "__main__.py").write_text("raise ImportError('broken')\n")  # python itself exits 1
    r, out, summary = gate(tmp_path, FIX / "candidate_regression.run.json", root=t)
    assert r.returncode == 2
    assert "No report" in summary or "comparison_invalid" in summary


@needs_bash
def test_missing_candidate_is_comparison_invalid(tmp_path):
    r, out, summary = gate(tmp_path, tmp_path / "absent.json")
    assert r.returncode == 2 and "unreadable_input" in (out / "report.json").read_text()


@needs_bash
def test_candidate_cannot_replace_gate_code_or_baseline(tmp_path):
    # Candidate checkout carries a "friendly" mrds and baseline; the script only ever reads its own (trusted) root.
    cand_tree = tmp_path / "candidate-src"
    (cand_tree / "mrds").mkdir(parents=True)
    (cand_tree / "mrds" / "__main__.py").write_text("raise SystemExit(0)\n")
    shutil.copy(FIX / "candidate_regression.run.json", cand_tree / "run.json")
    r, out, summary = gate(tmp_path, cand_tree / "run.json")
    assert r.returncode == 1  # trusted gate still sees the regression


# ----------------------------------------------------------------------------- workflow file
_RAW = (ROOT / ".github" / "workflows" / "model-regression-gate.yml").read_text()
WORKFLOW = "\n".join(l for l in _RAW.splitlines() if not l.lstrip().startswith("#")) + "\n"  # comments excluded


def test_workflow_uses_trusted_base_and_least_privilege():
    assert "pull_request_target" not in WORKFLOW
    assert re.search(r"^on:\n  pull_request:", WORKFLOW, re.M)
    assert "ref: ${{ github.event.pull_request.base.sha }}" in WORKFLOW and "path: trusted" in WORKFLOW
    assert re.search(r"^permissions:\n  contents: read$", WORKFLOW, re.M)
    assert WORKFLOW.count("persist-credentials: false") == 2
    assert "trusted/ci/run_gate.sh" in WORKFLOW and "candidate-src/candidate/run.json" in WORKFLOW
    assert "secrets." not in WORKFLOW


def test_workflow_actions_are_pinned_to_full_commit_shas():
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
    assert len(uses) == 3
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_workflow_publishes_even_when_the_gate_fails_and_labels_the_tolerance():
    assert re.search(r"if: \$\{\{ always\(\) \}\}\n\s+uses: actions/upload-artifact@", WORKFLOW)
    assert "synthetic fixture tolerance" in WORKFLOW
    gate_step = WORKFLOW.split("Run gate")[1].split("- name:")[0]
    assert "continue-on-error" not in WORKFLOW and "|| true" not in gate_step
