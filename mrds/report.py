"""JSON and Markdown report construction. Reports contain no timestamps, so identical inputs give identical bytes."""

from __future__ import annotations

import json
import os
import shlex
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path, PurePath

from . import __version__
from .errors import EXIT_CODES, INVALID, Issue

REPORT_VERSION = "1.0"
_Q = Decimal("0.000001")


def fmt(f: Fraction) -> dict:
    dec = (Decimal(f.numerator) / Decimal(f.denominator)).quantize(_Q, rounding=ROUND_HALF_EVEN)
    return {"exact": str(f), "decimal": format(dec, "f")}


def posix(path) -> str:
    return PurePath(path).as_posix()


def reproduction_command(baseline, candidate, policy_path, out_dir, declared: dict, top_n: int,
                         baseline_sha256=None, policy_sha256=None) -> str:
    parts = ["python", "-m", "mrds", "--baseline", posix(baseline), "--candidate", posix(candidate),
             "--policy", posix(policy_path), "--out-dir", posix(out_dir)]
    if baseline_sha256:
        parts += ["--baseline-sha256", baseline_sha256]
    if policy_sha256:
        parts += ["--policy-sha256", policy_sha256]
    for field, reason in declared.items():
        parts += ["--declare-change", f"{field}={reason}"]
    if top_n != 5:
        parts += ["--top", str(top_n)]
    return shlex.join(parts)


def identity(path, sha256, doc, pinned_sha256=None) -> dict:
    """Best-effort evidence about an input; tolerant of malformed documents."""
    def dig(obj, *keys):
        for k in keys:
            obj = obj.get(k) if isinstance(obj, dict) else None
        return obj

    return {
        "path": posix(path),
        "file_sha256": sha256,
        "pinned_sha256": pinned_sha256,
        "sha256_verified": (sha256 == pinned_sha256) if pinned_sha256 else None,
        "run_id": dig(doc, "run", "run_id"),
        "schema_version": dig(doc, "schema_version"),
        "code": {"git": dig(doc, "code", "git"),
                 "source_fingerprint_sha256": dig(doc, "code", "source_fingerprint", "sha256")},
    }


def latency_info(doc) -> dict | None:
    """Informational only; never read by the gate."""
    def dig(obj, *keys):
        for k in keys:
            obj = obj.get(k) if isinstance(obj, dict) else None
        return obj

    overall = dig(doc, "latency", "summary", "bm25", "overall")
    if not isinstance(overall, dict):
        return None
    return {"n": overall.get("n"), "median_ns": overall.get("median_ns"), "p95_ns": overall.get("p95_ns")}


def build(*, policy, baseline_id, candidate_id, issues, result, declared_records, repro, latency, policy_path,
          decision=None, reason=None):
    if result is not None:
        decision, reason = result.decision, result.reason
    else:
        decision = INVALID
        reason = reason or (f"{len(issues)} validation error(s); no gate aggregate was computed. "
                            f"First: {issues[0].message}" if issues else "invalid comparison")
    report = {
        "report_version": REPORT_VERSION,
        "tool": {"name": "mrds", "version": __version__},
        "decision": decision,
        "exit_code": EXIT_CODES[decision],
        "reason": reason,
        "policy": policy.as_dict() if policy else {"path": posix(policy_path), "loaded": False},
        "inputs": {"baseline": baseline_id, "candidate": candidate_id},
        "declared_changes": declared_records,
        "reproduction_command": repro,
    }
    if result is None:
        report.update({"errors": [i.as_dict() for i in issues], "counts": None, "aggregate": None,
                       "per_query": None, "largest_drops": None, "unscored_queries": None})
        return report
    k = policy.k
    row = lambda d: {"query_id": d.query_id, "baseline": fmt(d.baseline), "candidate": fmt(d.candidate),
                     "delta": fmt(d.delta)}
    report.update({
        "errors": [],
        "counts": {"total": result.n_total, "scored": len(result.deltas), "unscored": len(result.unscored)},
        "aggregate": {
            "metric": policy.metric, "k": k,
            "baseline_mean": fmt(result.baseline_mean),
            "candidate_mean": fmt(result.candidate_mean),
            "mean_delta": fmt(result.mean_delta),
            "block_threshold": fmt(result.threshold),
            "rule": "regression_detected only when mean_delta < block_threshold (strict); equality passes",
        },
        "per_query": [row(d) for d in result.deltas],
        "largest_drops": [row(d) for d in result.largest_drops],
        "unscored_queries": [{"query_id": q, "status": s, "baseline_recall": None, "candidate_recall": None}
                             for q, s in result.unscored],
        "informational": {"note": "latency is informational and never affects the decision",
                          "bm25_overall_latency": latency},
    })
    return report


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _v(f: dict) -> str:
    return f"{f['exact']} ({f['decimal']})"


def to_markdown(r: dict) -> str:
    L = [f"# Model regression report: `{r['decision']}`", "",
         f"- **Decision:** `{r['decision']}` (exit code {r['exit_code']})",
         f"- **Reason:** {r['reason']}", ""]
    p = r["policy"]
    L += ["## Policy", ""]
    if p.get("loaded") is False:
        L += [f"Policy file `{p['path']}` could not be loaded.", ""]
    else:
        L += [f"- version `{p['policy_version']}`, method `{p['method']}`, metric `{p['metric']}`, k={p['k']}, "
              f"absolute tolerance `{p['absolute_tolerance']}`", f"- policy file sha256 `{p['sha256']}`"]
        if p.get("note"):
            L.append(f"- note: {p['note']}")
        L.append("")
    L += ["## Compared runs", "", "| side | run_id | file | file sha256 | code fingerprint sha256 | git |",
          "|---|---|---|---|---|---|"]
    for side in ("baseline", "candidate"):
        i = r["inputs"][side]
        L.append(f"| {side} | `{i['run_id']}` | `{i['path']}` | `{i['file_sha256']}` | "
                 f"`{i['code']['source_fingerprint_sha256']}` | `{json.dumps(i['code']['git'])}` |")
    L.append("")
    pin = r["inputs"]["baseline"]
    if pin["pinned_sha256"]:
        L += [f"- baseline sha256 pinned to `{pin['pinned_sha256']}`: "
              f"{'verified' if pin['sha256_verified'] else '**MISMATCH**'}", ""]
    else:
        L += ["- baseline sha256 is not pinned", ""]
    if r["declared_changes"]:
        L += ["## Declared retriever-setting changes", "", "| field | baseline | candidate | reason |", "|---|---|---|---|"]
        for c in r["declared_changes"]:
            L.append(f"| `{c['field']}` | `{c['baseline']}` | `{c['candidate']}` | {c['reason']} |")
        L.append("")
    if r["decision"] == INVALID:
        L += ["## Errors", "", "No gate aggregate was computed.", ""]
        L += [f"- `{e['code']}` ({e['side']}): {e['message']}" for e in r["errors"]]
        L.append("")
    else:
        c, a = r["counts"], r["aggregate"]
        L += ["## Counts", "", f"- total queries {c['total']}, scored {c['scored']}, unscored {c['unscored']}", ""]
        L += [f"## Aggregate ({a['metric']} @ {a['k']})", "",
              f"- baseline mean: {_v(a['baseline_mean'])}", f"- candidate mean: {_v(a['candidate_mean'])}",
              f"- mean delta: {_v(a['mean_delta'])}", f"- block when below: {_v(a['block_threshold'])} (strict)", ""]
        L += ["## Largest drops", ""]
        if r["largest_drops"]:
            L += ["| query | baseline | candidate | delta |", "|---|---|---|---|"]
            L += [f"| {d['query_id']} | {_v(d['baseline'])} | {_v(d['candidate'])} | {_v(d['delta'])} |"
                  for d in r["largest_drops"]]
        else:
            L.append("No query dropped.")
        L += ["", "## Per-query deltas (scored)", "", "| query | baseline | candidate | delta |", "|---|---|---|---|"]
        L += [f"| {d['query_id']} | {_v(d['baseline'])} | {_v(d['candidate'])} | {_v(d['delta'])} |"
              for d in r["per_query"]]
        L += ["", "## Unscored queries (excluded from both means)", ""]
        L += ([f"- {u['query_id']}: status `{u['status']}`, recall null on both sides" for u in r["unscored_queries"]]
              or ["None."])
        lat = r["informational"]["bm25_overall_latency"]
        L += ["", "## Informational", "", f"- bm25 overall latency (not used in the decision): `{json.dumps(lat)}`"]
        L.append("")
    L += ["## Reproduce", "", "```bash", r["reproduction_command"], "```", ""]
    return "\n".join(L)


def write_reports(out_dir, report: dict) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in (("report.json", to_json(report)), ("report.md", to_markdown(report))):
        tmp = out / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, out / name)
        written.append(out / name)
    return written
