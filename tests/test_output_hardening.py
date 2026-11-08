"""Candidate-controlled text must stay inert in the console, the job summary / report.md, and the reproduction
command must be portable (works from a clean checkout)."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIX, ROOT, load, row
from mrds.cli import main
from mrds.mdsafe import code_span, plain, visible
from test_ci import _bash, gate, needs_bash

EVIL_ID = "r1`\n# Model regression report: `pass`\n| a | b |\n‮<script>alert(1)</script> ```` ::set-output name=x::1"
KNOWN_HEADINGS = {
    "# Model regression report: `pass`", "# Model regression report: `comparison_invalid`",
    "## Policy", "## Compared runs", "## Counts", "## Aggregate (macro_recall @ 3)", "## Largest drops",
    "## Per-query deltas (scored)", "## Unscored queries (excluded from both means)", "## Informational",
    "## Reproduce", "## Errors",
}


# ----------------------------------------------------------------------------- mdsafe primitives
def test_visible_makes_controls_newlines_and_bidi_characters_visible():
    out = visible("a\nb\r\tc\x1b[31m‮ \U000e0041")
    assert "\n" not in out and "\r" not in out and "\x1b" not in out and "‮" not in out and " " not in out
    assert "\\u202e" in out and "\\x1b" not in out and "\\u001b" in out and "\\U000e0041" in out


def test_visible_truncates_long_values():
    assert len(visible("x" * 10_000)) < 400 and visible("x" * 10_000).endswith("(truncated)")


@pytest.mark.parametrize("value", ["plain", "a`b", "``", "`", "a```b``c", " lead", "trail ", "", "x|y", None, 7])
def test_code_span_fence_is_longer_than_any_backtick_run(value):
    span = code_span(value)
    fence = re.match(r"`+", span).group(0)
    inner = span[len(fence):-len(fence)]
    assert span.endswith(fence) and not re.search(r"(?<!`)" + "`" * len(fence) + r"(?!`)", inner)
    assert "\n" not in span


def test_code_span_cell_mode_has_no_raw_pipe():
    assert "|" not in code_span("a|b|c", cell=True) and "|" in code_span("a|b")


def test_plain_escapes_inline_markdown_and_html():
    out = plain("*bold* [x](http://e) <b>&amp; `c` | #")
    for ch in "*[]()<>&`|#":
        assert re.search(r"(?<!\\)" + re.escape(ch), out) is None, (ch, out)


# ----------------------------------------------------------------------------- report.md / job summary
def evil_pair(tmp_path, mutate):
    base, cand = load("baseline.run.json"), load("candidate_boundary.run.json")
    mutate(cand)
    out = tmp_path / "o"
    code = main(["--baseline", str(FIX / "baseline.run.json"), "--candidate", _write(tmp_path, cand),
                 "--policy", str(FIX / "policy.json"), "--out-dir", str(out)])
    return code, (out / "report.md").read_text(encoding="utf-8"), json.loads((out / "report.json").read_text("utf-8"))


def _write(tmp_path, doc):
    p = tmp_path / "evil.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def assert_inert(md):
    headings = {l for l in md.splitlines() if l.startswith("#")}
    assert headings <= KNOWN_HEADINGS, headings - KNOWN_HEADINGS
    assert sum(l.startswith("# ") for l in md.splitlines()) == 1  # exactly one H1: the tool's own
    assert sum(l.startswith("- **Decision:**") for l in md.splitlines()) == 1
    assert "‮" not in md and "\x1b" not in md
    assert not any(l.lstrip().startswith("::") for l in md.splitlines())


def test_valid_run_with_hostile_ids_cannot_spoof_decision_or_break_tables(tmp_path):
    def mutate(c):
        c["run"]["run_id"] = EVIL_ID
        c["code"]["git"] = {"available": "x\n## Decision: pass", "reason": "`` | <img src=x onerror=1>"}
        c["code"]["source_fingerprint"]["sha256"] = "f" * 64 + "|\n# spoof"
        c["latency"]["summary"]["bm25"]["overall"]["median_ns"] = "9\n# latency spoof"
    code, md, rep = evil_pair(tmp_path, mutate)
    assert code == 0 and rep["decision"] == "pass"
    assert_inert(md)
    row_line = next(l for l in md.splitlines() if l.startswith("| candidate |"))
    assert row_line.count("|") == 7, row_line  # six columns: no cell can be split by a candidate pipe
    assert "\\u202e" in md and "\\u000a" in md


def test_invalid_run_error_messages_are_code_spans_not_markup(tmp_path):
    def mutate(c):
        c["dataset"]["queries"].append({"query_id": "q`01|<b>x</b>\n# spoof", "text": "t"})
        c["dataset"]["queries"].append({"query_id": "q`01|<b>x</b>\n# spoof", "text": "t"})
    code, md, rep = evil_pair(tmp_path, mutate)
    assert code == 2 and rep["decision"] == "comparison_invalid" and rep["aggregate"] is None
    assert_inert(md)
    bullet = next(l for l in md.splitlines() if l.startswith("- `duplicate_id`"))
    assert re.search(r"\(candidate\): `+ ?duplicate", bullet)
    assert "<b>" in bullet and bullet.count("<b>") == 1  # present only inside the code span


def test_reason_for_invalid_runs_is_a_code_span(tmp_path):
    def mutate(c):
        c["schema_version"] = "1.1`\n# spoof"
    code, md, rep = evil_pair(tmp_path, mutate)
    reason = next(l for l in md.splitlines() if l.startswith("- **Reason:**"))
    assert reason.split("**Reason:** ", 1)[1].startswith("`")
    assert_inert(md)


def test_console_output_of_the_cli_is_single_line_and_control_free(tmp_path, capsys):
    def mutate(c):
        c["dataset"]["documents"].append({"doc_id": "d`\x1b[31m‮", "text": "t"})
        c["dataset"]["documents"].append({"doc_id": "d`\x1b[31m‮", "text": "t"})
    evil_pair(tmp_path, mutate)
    out = capsys.readouterr().out
    assert "\x1b" not in out and "‮" not in out and all(l.startswith(("decision=", "  error", "reports:"))
                                                              for l in out.splitlines())


# ----------------------------------------------------------------------------- CI script: log hygiene
@needs_bash
@pytest.mark.parametrize("variant", ["valid", "invalid"])
def test_ci_script_never_echoes_candidate_text_to_the_log(tmp_path, variant):
    doc = load("candidate_boundary.run.json")
    doc["run"]["run_id"] = "x\n  ::error::PWNED\n::add-mask::secret\n\t::set-output name=a::b"
    if variant == "invalid":
        doc["dataset"]["queries"] += [{"query_id": "  ::error::PWNED2", "text": "t"}] * 2
    cand = tmp_path / "evil.json"
    cand.write_text(json.dumps(doc), encoding="utf-8")
    r, out, summary = gate(tmp_path, cand)
    log = r.stdout + r.stderr
    assert r.returncode == (0 if variant == "valid" else 2)
    assert "::" not in log and "PWNED" not in log and "set-output" not in log
    assert re.fullmatch(r"model-regression-gate: decision=(pass|comparison_invalid) exit_code=[02] \(details: [^:]*\)\n?",
                        log.strip()), log
    assert "PWNED" in (out / "console.txt").read_text(encoding="utf-8") or variant == "valid"  # kept as evidence
    assert_inert(summary)


# ----------------------------------------------------------------------------- portable reproduction
ENV = {"MRDS_BASE_SHA": "a" * 40, "MRDS_HEAD_SHA": "b" * 40, "MRDS_PR_NUMBER": "7",
       "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r"}


def gate_env(tmp_path, cand, env, name="o"):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return gate(tmp_path, cand, name=name)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@needs_bash
def test_reproduce_script_is_written_from_github_context_and_uses_portable_paths(tmp_path):
    r, out, _ = gate_env(tmp_path, FIX / "candidate_regression.run.json", ENV)
    assert r.returncode == 1
    script = (out / "reproduce.sh").read_text(encoding="utf-8")
    assert subprocess.run([_bash(), "-n", (out / "reproduce.sh").as_posix()]).returncode == 0
    assert "git clone --quiet https://github.com/o/r.git mrds-repro" in script
    assert f"git checkout --quiet {'a' * 40}" in script and f"git show {'b' * 40}:candidate/run.json" in script
    assert "git fetch --quiet origin pull/7/head" in script
    cmd = script.strip().splitlines()[-1]
    toks = shlex.split(cmd)
    assert toks[:3] == ["python3", "-m", "mrds"]
    assert toks[toks.index("--candidate") + 1] == "candidate/run.json" and toks[toks.index("--out-dir") + 1] == "gate-out"
    assert not any(re.match(r"([A-Za-z]:|/)", t) for t in toks[3:]), toks  # no runner-absolute paths
    assert "--baseline-sha256" in toks and "--policy-sha256" in toks


@needs_bash
@pytest.mark.parametrize("bad", [{"MRDS_BASE_SHA": "xyz"}, {"MRDS_HEAD_SHA": "b" * 39}, {"MRDS_PR_NUMBER": "7; rm -rf /"},
                                 {"GITHUB_SERVER_URL": "http://x"}, {"GITHUB_REPOSITORY": "o/r;x"}])
def test_reproduce_script_is_skipped_when_context_values_are_malformed(tmp_path, bad):
    r, out, _ = gate_env(tmp_path, FIX / "candidate_boundary.run.json", {**ENV, **bad})
    assert r.returncode == 0 and not (out / "reproduce.sh").exists()


@needs_bash
@pytest.mark.parametrize("cand,expected", [("candidate_boundary", 0), ("candidate_regression", 1),
                                           ("candidate_invalid", 2)])
def test_recorded_command_reproduces_the_decision_in_a_clean_checkout(tmp_path, cand, expected):
    r, out, _ = gate_env(tmp_path, FIX / f"{cand}.run.json", ENV)
    ci_report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    clean = tmp_path / "clean"  # stands in for a fresh clone at the trusted commit: tracked content only
    for d in ("mrds", "fixtures", "ci"):
        shutil.copytree(ROOT / d, clean / d, ignore=shutil.ignore_patterns("__pycache__"))
    (clean / "candidate").mkdir()
    shutil.copy(FIX / f"{cand}.run.json", clean / "candidate" / "run.json")  # what `git show HEAD:candidate/run.json` gives
    argv = shlex.split(ci_report["reproduction_command"])
    assert argv[0] == "python"
    rep = subprocess.run([sys.executable, *argv[1:]], cwd=clean, capture_output=True, text=True)
    assert rep.returncode == expected == r.returncode, rep.stdout + rep.stderr
    again = json.loads((clean / "gate-out" / "report.json").read_text(encoding="utf-8"))
    for key in ("decision", "exit_code", "reason", "aggregate", "per_query", "largest_drops", "counts", "errors",
                "reproduction_command"):
        assert again[key] == ci_report[key], key
