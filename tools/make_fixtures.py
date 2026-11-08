"""Deterministic generator for the synthetic schema-1.1 fixtures in fixtures/synthetic-v1/.

Field layout follows examples/demo/run.json at harness commit 5daa6003131c1ce3e11a1673c976c24dac2c80c7, reduced to
one method (bm25), ks=[3], max_k=3. Rankings are made up (not produced by BM25); each file records the intended
change under `synthetic_fixture`. No clocks, randomness or environment input: reruns are byte-identical.

    python tools/make_fixtures.py [out_dir]
"""

from __future__ import annotations

import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

HARNESS_COMMIT = "5daa6003131c1ce3e11a1673c976c24dac2c80c7"
DOC_IDS = [f"d{i:02d}" for i in range(1, 14)]
QUERY_IDS = ["q01", "q02", "q08"]
RELEVANT = {
    "q01": ["d01", "d02", "d03", "d04", "d05", "d06", "d07", "d08", "d09", "d10"],
    "q02": ["d11"],
    "q08": [],
}
BASELINE = {"q01": ["d01", "d02", "d03"], "q02": ["d11", "d12", "d13"], "q08": ["d01", "d02", "d03"]}
BOUNDARY = {"q01": ["d01", "d02", "d12"], "q02": ["d11", "d12", "d13"], "q08": ["d01", "d02", "d03"]}
REGRESSION = {"q01": ["d01", "d12", "d13"], "q02": ["d11", "d12", "d13"], "q08": ["d01", "d02", "d03"]}
INVALID = {"q02": ["d11", "d12", "d13"], "q08": ["d01", "d02", "d03"]}

POLICY = {
    "policy_version": "synthetic-v1",
    "method": "bm25",
    "metric": "macro_recall",
    "k": 3,
    "absolute_tolerance": "0.05",
    "note": "fixture-only tolerance; real tolerance and baseline approval are pending",
}

# Copied verbatim from the pinned example's `policies` block.
POLICIES = {
    "metrics": "Recall@k and RR@k per query; macro Recall@k and MRR@k = arithmetic mean over queries with at least one judged-relevant document (same query set and denominator for every method).",
    "no_judged_relevant": "metrics are null (JSON null / blank CSV), status 'no_judged_relevant'; excluded from macro means; counted in 'no_judged_relevant'. A null is never a zero.",
    "empty_ranking_for_answerable_query": "scores 0 and stays in the macro denominator.",
    "unjudged_documents": "contribute zero gain, keep their rank, labeled 'unjudged' (distinct from an explicit 0 judgment, labeled 'nonrelevant').",
    "ranking_order": "score descending, then doc_id ascending (case-sensitive), applied to the full ranking before truncation to max_k. Scores are comparable only within one method.",
    "ranking_consistency": "Every first-call, warm-up and measured search must return the same ORDERED document IDs as the untimed quality-pass ranking of that (method, query). A different ordered ID list makes the run incomplete (stage 'consistency', run_failed.json with the diagnostic, no run.json). If the IDs match but scores differ, the run may complete and the differences are reported separately.",
    "json": "NaN/Infinity are rejected; absent values are null.",
}

BM25 = {"name": "bm25", "library": "rank-bm25", "library_version": "0.2.2", "variant": "BM25Okapi", "k1": 1.5,
        "b": 0.75, "epsilon": 0.25, "tokenizer_pattern": "[a-z0-9]+",
        "tokenizer_normalization": "str.casefold() before matching", "stemming": False, "stop_words": None,
        "n_documents": 13}

SCENARIOS = {
    "baseline": dict(rankings=BASELINE, latency_base=100_000,
                     change="none: baseline. q01 ranks 3 of 10 relevant docs in top 3 (0.3); q02 1.0."),
    "candidate_boundary": dict(rankings=BOUNDARY, latency_base=110_000,
                               change="q01 rank 3 changes d03 (relevant) -> d12 (nonrelevant): recall 0.3 -> 0.2; "
                                      "mean 0.65 -> 0.60, delta exactly -0.05."),
    "candidate_regression": dict(rankings=REGRESSION, latency_base=120_000,
                                 change="q01 ranks 2-3 change d02,d03 (relevant) -> d12,d13 (nonrelevant): recall "
                                        "0.3 -> 0.1; mean 0.65 -> 0.55, delta -0.10."),
    "candidate_invalid": dict(rankings=INVALID, latency_base=130_000,
                              change="q01 result row omitted from results.bm25.queries while q01 stays in "
                                     "dataset.queries and dataset.judgments (saved aggregate would be q02 alone: 1.0)."),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dataset_block() -> dict:
    docs = [{"doc_id": d, "text": f"Synthetic document {d}."} for d in DOC_IDS]
    queries = [{"query_id": q, "text": f"Synthetic query {q}."} for q in QUERY_IDS]
    judgments = [[q, d, 1 if d in RELEVANT[q] else 0] for q in QUERY_IDS for d in DOC_IDS]
    docs_bytes = "".join(json.dumps(d, sort_keys=True) + "\n" for d in docs).encode()
    queries_bytes = "".join(json.dumps(q, sort_keys=True) + "\n" for q in queries).encode()
    qrels_bytes = ("query_id,doc_id,relevance\n" + "".join(f"{q},{d},{r}\n" for q, d, r in judgments)).encode()
    files = [{"name": n, "sha256": sha(b), "bytes": len(b)}
             for n, b in (("documents.jsonl", docs_bytes), ("queries.jsonl", queries_bytes), ("qrels.csv", qrels_bytes))]
    return {
        "designation": "synthetic", "synthetic_developer_fixture": True, "directory": "synthetic-v1 (generated)",
        "files": files, "fixture_manifest": {"name": "FIXTURE.sha256", "present": False, "verified": False},
        "counts": {"documents": len(docs), "queries": len(queries), "judgments": len(judgments),
                   "positive_judgments": sum(r for _, _, r in judgments), "queries_without_relevant": 1},
        "documents": docs, "queries": queries, "judgments": judgments,
    }


def query_row(qid: str, ranking_ids: list[str]) -> dict:
    rel = set(RELEVANT[qid])
    ranking = [{"rank": i, "doc_id": d, "score": float(4 - i),
                "label": "relevant" if d in rel else "nonrelevant"} for i, d in enumerate(ranking_ids, 1)]
    if rel:
        recall = float(Fraction(len(rel.intersection(ranking_ids[:3])), len(rel)))
        first = next((i for i, d in enumerate(ranking_ids[:3], 1) if d in rel), None)
        rr = 1.0 / first if first else 0.0
    else:
        recall = rr = None
    return {
        "query_id": qid, "status": "scored" if rel else "no_judged_relevant", "n_relevant": len(rel),
        "n_returned": len(ranking), "metrics": {"3": {"recall": recall, "rr": rr}}, "ranking": ranking,
        "measured_search_consistency": {"n_measured": 1, "n_identical_to_quality_pass": 1, "n_ranking_identical": 1,
                                        "n_score_differs_same_ranking": 0, "max_abs_score_difference": 0.0},
    }


def build_run(name: str, rankings: dict, latency_base: int, change: str) -> dict:
    rows = [query_row(q, rankings[q]) for q in QUERY_IDS if q in rankings]
    scored = [r for r in rows if r["status"] == "scored"]
    n = len(scored)
    macro = float(sum(Fraction(str(r["metrics"]["3"]["recall"])) for r in scored) / n) if n else None
    mrr = sum(r["metrics"]["3"]["rr"] for r in scored) / n if n else None
    run_id = sha(f"synthetic-v1/{name}".encode())[:32]
    samples = [{"method": "bm25", "query_id": r["query_id"], "repetition": 1, "execution_index": i,
                "position_in_pair": 1, "elapsed_ns": latency_base + 1000 * i} for i, r in enumerate(rows, 1)]
    ns = [s["elapsed_ns"] for s in samples]
    return {
        "schema_version": "1.1",
        "synthetic_fixture": {"scenario": name, "generator": "tools/make_fixtures.py",
                              "field_reference": f"retrieval-eval-harness@{HARNESS_COMMIT} examples/demo/run.json",
                              "intended_ranking_change": change,
                              "warning": "hand-written rankings, not produced by running BM25"},
        "run": {"run_id": run_id, "status": "completed", "started_utc": "2026-01-01T00:00:00+00:00",
                "finished_utc": "2026-01-01T00:00:01+00:00", "command": ["python", "tools/make_fixtures.py"]},
        "config": {"dataset_dir": "synthetic-v1", "ks": [3], "max_k": 3, "warmup_per_query_method": 0,
                   "measured_per_query_method": 1, "threads_requested": 1, "device": "cpu", "methods": ["bm25"]},
        "policies": POLICIES,
        "environment": {"python_version": "synthetic", "platform": "synthetic"},
        "dependencies": {},
        "code": {"git": {"available": False, "reason": "synthetic fixture"},
                 "source_fingerprint": {"sha256": sha(f"synthetic-code/{name}".encode()), "files": []}},
        "adapters": {"bm25": BM25},
        "model": None,
        "timing_protocol": {"clock": "time.perf_counter_ns", "unit": "ns", "k_searched": 3,
                            "warmup_per_query_method": 0, "measured_per_query_method": 1,
                            "query_order": list(QUERY_IDS)},
        "ranking_consistency": {"rule": policies_rule(), "calls_checked": len(rows), "ranking_disagreements": 0,
                                "score_only_differences": 0, "max_abs_score_difference": 0.0},
        "setup_observations": {"bm25": {"bm25_index_build_ns": 1_000_000}},
        "first_call_observations": [],
        "dataset": dataset_block(),
        "latency": {"unit": "ns", "samples": samples, "warmup_samples": [],
                    "summary": {"bm25": {
                        "overall": {"n": len(ns), "median_ns": float(sorted(ns)[len(ns) // 2]), "min_ns": min(ns),
                                    "max_ns": max(ns), "p95_ns": float(max(ns))},
                        "per_query": {s["query_id"]: {"n": 1, "median_ns": float(s["elapsed_ns"]),
                                                      "min_ns": s["elapsed_ns"], "max_ns": s["elapsed_ns"]}
                                      for s in samples}}}},
        "results": {"bm25": {
            "counts": {"total": len(rows), "scored": n, "no_judged_relevant": len(rows) - n},
            "aggregate": {"3": {"macro_recall": macro, "mrr": mrr}},
            "queries": rows}},
    }


def policies_rule() -> str:
    return POLICIES["ranking_consistency"]


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_all(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, s in SCENARIOS.items():
        p = out / f"{name}.run.json"
        p.write_text(dumps(build_run(name, s["rankings"], s["latency_base"], s["change"])), encoding="utf-8",
                     newline="\n")
        written.append(p)
    p = out / "policy.json"
    p.write_text(dumps(POLICY), encoding="utf-8", newline="\n")
    written.append(p)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "fixtures" / "synthetic-v1"
    for path in write_all(target):
        print(path)
