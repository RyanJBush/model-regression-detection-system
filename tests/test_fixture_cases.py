"""The three headline fixture assertions, the exact boundary, report content and determinism."""

import subprocess
import sys
from fractions import Fraction

import pytest

from conftest import FIX, ROOT, load
from mrds.gate import decide
from tools import make_fixtures


def test_boundary_passes_with_exact_minus_005(run_cli, base):
    code, rep = run_cli(base, load("candidate_boundary.run.json"))
    assert (code, rep["decision"], rep["exit_code"]) == (0, "pass", 0)
    a = rep["aggregate"]
    assert a["baseline_mean"]["exact"] == "13/20" and a["candidate_mean"]["exact"] == "3/5"
    assert a["mean_delta"]["exact"] == "-1/20"
    assert a["block_threshold"]["exact"] == "-1/20"
    assert rep["counts"] == {"total": 3, "scored": 2, "unscored": 1}
    assert rep["unscored_queries"] == [{"query_id": "q08", "status": "no_judged_relevant",
                                        "baseline_recall": None, "candidate_recall": None}]
    assert [d["query_id"] for d in rep["largest_drops"]] == ["q01"]
    assert rep["largest_drops"][0]["delta"]["exact"] == "-1/10"


def test_regression_blocks_and_names_largest_drop(run_cli, base):
    code, rep = run_cli(base, load("candidate_regression.run.json"))
    assert (code, rep["decision"]) == (1, "regression_detected")
    assert rep["aggregate"]["mean_delta"]["exact"] == "-1/10"
    assert rep["aggregate"]["candidate_mean"]["decimal"] == "0.550000"
    top = rep["largest_drops"][0]
    assert (top["query_id"], top["delta"]["exact"], top["delta"]["decimal"]) == ("q01", "-1/5", "-0.200000")


def test_invalid_missing_q01_is_rejected_before_aggregating(run_cli, base):
    cand = load("candidate_invalid.run.json")
    # the saved aggregate over q02 alone is 1.0: the tempting wrong answer
    assert cand["results"]["bm25"]["aggregate"]["3"]["macro_recall"] == 1.0
    code, rep = run_cli(base, cand)
    assert (code, rep["decision"]) == (2, "comparison_invalid")
    assert rep["aggregate"] is None and rep["per_query"] is None and rep["counts"] is None
    missing = [e for e in rep["errors"] if e["code"] == "missing_result"]
    assert len(missing) == 1 and "q01" in missing[0]["message"] and missing[0]["side"] == "candidate"


def test_exact_boundary_uses_rationals_not_floats():
    tol = Fraction("0.05")
    assert decide(Fraction(-1, 20), tol) == "pass"
    assert decide(Fraction(-1, 20) - Fraction(1, 10**12), tol) == "regression_detected"
    # 0.1 + 0.2 style float drift can not matter: thirds sum exactly
    assert decide(sum([Fraction(-1, 3)] * 3, Fraction(0)) / 3 + Fraction(1, 3) - Fraction(1, 20), tol) == "pass"


def test_report_carries_required_evidence(run_cli, base):
    code, rep = run_cli(base, load("candidate_regression.run.json"))
    assert rep["policy"]["policy_version"] == "synthetic-v1" and rep["policy"]["absolute_tolerance"] == "0.05"
    b, c = rep["inputs"]["baseline"], rep["inputs"]["candidate"]
    assert b["run_id"] != c["run_id"] and b["run_id"] == base["run"]["run_id"]
    assert b["code"]["source_fingerprint_sha256"] != c["code"]["source_fingerprint_sha256"]
    assert len(b["file_sha256"]) == 64
    assert rep["reproduction_command"].startswith("python -m mrds --baseline ")
    assert "latency is informational" in rep["informational"]["note"]


def test_markdown_report_matches_json(tmp_path):
    out = tmp_path / "o"
    from mrds.cli import main
    main(["--baseline", str(FIX / "baseline.run.json"), "--candidate", str(FIX / "candidate_regression.run.json"),
          "--policy", str(FIX / "policy.json"), "--out-dir", str(out)])
    md = (out / "report.md").read_text(encoding="utf-8")
    for needle in ("regression_detected", "Largest drops", "| `q01` |", "-1/5 (-0.200000)", "q08", "python -m mrds"):
        assert needle in md


def test_reports_are_byte_deterministic(tmp_path):
    from mrds.cli import main
    args = ["--baseline", "fixtures/synthetic-v1/baseline.run.json", "--candidate",
            "fixtures/synthetic-v1/candidate_regression.run.json", "--policy", "fixtures/synthetic-v1/policy.json"]
    outs = []
    for i in (1, 2):
        main(args + ["--out-dir", str(tmp_path / f"o{i}")])
        outs.append([(tmp_path / f"o{i}" / n).read_bytes() for n in ("report.json", "report.md")])
    # reproduction command embeds the out dir, so compare everything else
    strip = lambda b, i: b.replace(f"o{i}".encode(), b"O")
    assert strip(outs[0][0], 1) == strip(outs[1][0], 2) and strip(outs[0][1], 1) == strip(outs[1][1], 2)


def test_fixtures_regenerate_byte_identically(tmp_path):
    make_fixtures.write_all(tmp_path)
    for p in tmp_path.iterdir():
        assert p.read_bytes() == (FIX / p.name).read_bytes(), p.name


@pytest.mark.parametrize("cand_name,expected", [("candidate_boundary", 0), ("candidate_regression", 1),
                                                ("candidate_invalid", 2)])
def test_module_entry_point_exit_codes(tmp_path, cand_name, expected):
    r = subprocess.run([sys.executable, "-m", "mrds", "--baseline", "fixtures/synthetic-v1/baseline.run.json",
                        "--candidate", f"fixtures/synthetic-v1/{cand_name}.run.json", "--policy",
                        "fixtures/synthetic-v1/policy.json", "--out-dir", str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == expected, r.stdout + r.stderr


def test_unexpected_crash_exits_2_not_1(run_cli, base, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("mrds.gate.evaluate", boom)
    code, rep = run_cli(base, load("candidate_boundary.run.json"))
    assert code == 2 and rep["decision"] == "comparison_invalid"
    assert rep["errors"][0]["code"] == "internal_error" and "boom" in rep["errors"][0]["message"]
