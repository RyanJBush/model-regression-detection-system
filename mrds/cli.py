"""Command line entry point.

Exit codes: 0 pass, 1 regression_detected, 2 comparison_invalid. Any unexpected failure also exits 2, never 1,
so a crash can not be mistaken for a detected regression.
"""

from __future__ import annotations

import argparse
import re
import sys

from . import gate, report as rpt
from .errors import EXIT_CODES, INVALID, Issue
from .mdsafe import visible
from .policy import load_policy
from .validate import check_pair, check_run, load_run_file


def _declared(values):
    out = {}
    for v in values or []:
        field, sep, reason = v.partition("=")
        if not sep or not field or not reason.strip():
            raise argparse.ArgumentTypeError(f"--declare-change needs FIELD=REASON, got {v!r}")
        if field in out:
            raise argparse.ArgumentTypeError(f"--declare-change {field} given twice")
        out[field] = reason.strip()
    return out


def build_parser():
    p = argparse.ArgumentParser(prog="mrds", description="Gate a candidate run.json against a baseline "
                                                         "(Retrieval Evaluation Harness schema 1.1).")
    p.add_argument("--baseline", required=True, help="baseline run.json")
    p.add_argument("--candidate", required=True, help="candidate run.json")
    p.add_argument("--policy", required=True, help="policy JSON file")
    p.add_argument("--out-dir", required=True, help="directory for report.json and report.md (overwritten)")
    p.add_argument("--declare-change", action="append", metavar="FIELD=REASON",
                   help="declare an intended change of an adapters.<method> setting; repeatable")
    p.add_argument("--top", type=int, default=5, help="how many largest drops to list (default 5)")
    p.add_argument("--baseline-sha256", type=_sha256_arg, metavar="HEX",
                   help="pinned SHA-256 of the approved baseline file; a mismatch is comparison_invalid")
    p.add_argument("--policy-sha256", type=_sha256_arg, metavar="HEX",
                   help="pinned SHA-256 of the approved policy file; a mismatch is comparison_invalid")
    p.add_argument("--repro-candidate", metavar="PATH",
                   help="display-only: candidate path shown in the reproduction command (default: --candidate)")
    p.add_argument("--repro-out-dir", metavar="PATH",
                   help="display-only: output path shown in the reproduction command (default: --out-dir)")
    return p


def _sha256_arg(value):
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("expected 64 lowercase hex characters")
    return value


def _repro(args, declared) -> str:
    return rpt.reproduction_command(args.baseline, args.repro_candidate or args.candidate, args.policy,
                                    args.repro_out_dir or args.out_dir, declared, args.top, args.baseline_sha256,
                                    args.policy_sha256)


def analyse(args, declared) -> dict:
    """Validate, then (only if valid) decide. Returns the report dict."""
    repro = _repro(args, declared)
    policy, issues = load_policy(args.policy)
    if policy is not None and args.policy_sha256 and policy.sha256 != args.policy_sha256:
        issues.append(Issue("policy_hash_mismatch", "policy", f"policy file sha256 {policy.sha256} != pinned "
                                                              f"{args.policy_sha256}"))
    b_doc, b_sha, i = load_run_file(args.baseline, "baseline")
    issues += i
    if b_sha is not None and args.baseline_sha256 and b_sha != args.baseline_sha256:
        issues.append(Issue("baseline_hash_mismatch", "baseline", f"baseline file sha256 {b_sha} != pinned "
                                                                  f"{args.baseline_sha256}"))
    c_doc, c_sha, i = load_run_file(args.candidate, "candidate")
    issues += i
    base = cand = None
    if policy is not None:  # run checks need the policy's method and cutoff
        if b_doc is not None:
            base, i = check_run(b_doc, "baseline", args.baseline, b_sha, policy)
            issues += i
        if c_doc is not None:
            cand, i = check_run(c_doc, "candidate", args.candidate, c_sha, policy)
            issues += i
    records, result = [], None
    if not issues and base is not None and cand is not None:
        i, records = check_pair(base, cand, declared)
        issues += i
        if not issues:
            result = gate.evaluate(base, cand, policy, args.top)
    return rpt.build(
        policy=policy, policy_path=args.policy,
        baseline_id=rpt.identity(args.baseline, b_sha, b_doc, args.baseline_sha256), candidate_id=rpt.identity(args.candidate, c_sha, c_doc),
        issues=issues, result=result, declared_records=records if result else [], repro=repro,
        latency={"baseline": rpt.latency_info(b_doc), "candidate": rpt.latency_info(c_doc)} if result else None,
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # usage errors exit 2 via argparse
    try:
        declared = _declared(args.declare_change)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if args.top < 1:
        parser.error("--top must be >= 1")
    try:
        report = analyse(args, declared)
    except Exception as exc:  # noqa: BLE001 - deliberate: a crash must map to exit 2
        issue = Issue("internal_error", "cli", f"{type(exc).__name__}: {exc}")
        report = rpt.build(
            policy=None, policy_path=args.policy, baseline_id=rpt.identity(args.baseline, None, None),
            candidate_id=rpt.identity(args.candidate, None, None), issues=[issue], result=None, declared_records=[],
            repro=_repro(args, declared),
            latency=None,
        )
    try:
        rpt.write_reports(args.out_dir, report)
    except OSError as exc:
        print(f"mrds: cannot write reports to {args.out_dir}: {exc}", file=sys.stderr)
        return 2
    code = EXIT_CODES[report["decision"]]
    # candidate-derived text is made inert (no control characters, no line breaks) before it reaches a terminal or log
    print(f"decision={report['decision']} exit_code={code} reason={visible(report['reason'])}")
    for e in report["errors"]:
        print(f"  error[{e['code']}] {e['side']}: {visible(e['message'])}")
    print(f"reports: {rpt.posix(args.out_dir)}/report.json, {rpt.posix(args.out_dir)}/report.md")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
