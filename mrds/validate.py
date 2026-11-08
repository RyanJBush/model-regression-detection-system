"""Validation of schema-1.1 run.json documents and of a baseline/candidate pair.

Nothing here computes a gate decision. Recall is re-derived from rankings and judgments only to check the saved
values; the gate (gate.py) uses the re-derived exact fractions, never the saved floats.

Field paths follow the pinned harness example (commit 5daa600):
  dataset.{designation,files,documents,queries,judgments}, config.{ks,max_k,methods}, policies, adapters.<method>,
  code, results.<method>.{queries,counts,aggregate}.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .errors import Issue
from .policy import Policy

SUPPORTED_SCHEMA = "1.1"
FLOAT_TOLERANCE = 1e-12  # same tolerance the harness's verify_saved_run uses for saved-vs-recomputed metrics
STATUSES = ("scored", "no_judged_relevant")
LABELS = {1: "relevant", 0: "nonrelevant", None: "unjudged"}
REQUIRED_DATASET_FILES = ("documents.jsonl", "queries.jsonl", "qrels.csv")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    status: str
    n_relevant: int
    n_returned: int
    ranking: tuple
    recalls: dict  # k -> Fraction | None, re-derived from ranking + judgments


@dataclass(frozen=True)
class Run:
    side: str
    path: str
    file_sha256: str
    run_id: str
    code: dict
    ks: tuple
    max_k: int
    designation: str
    files: tuple  # sorted (name, sha256)
    documents: tuple  # sorted (doc_id, text)
    queries: tuple  # sorted (query_id, text)
    judgments: tuple  # sorted (query_id, doc_id, relevance)
    policies: object
    adapter: dict
    results: dict  # query_id -> QueryResult


# --------------------------------------------------------------------------- loading
def _reject_constant(name):
    raise ValueError(f"non-standard JSON constant {name!r}")


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON object key {key!r}")
        out[key] = value
    return out


def load_run_file(path, side):
    """Return (document | None, sha256-of-bytes | None, issues)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, None, [Issue("unreadable_input", side, f"cannot read {path}: {exc.strerror or exc}")]
    sha = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        return None, sha, [Issue("invalid_json", side, f"{path} is not strict JSON: {exc}")]
    return doc, sha, []


# --------------------------------------------------------------------------- single-run checks
class _Ctx:
    def __init__(self, side):
        self.side = side
        self.issues: list[Issue] = []

    def err(self, code, msg):
        self.issues.append(Issue(code, self.side, msg))

    def obj(self, parent, key, where):
        value = parent.get(key) if isinstance(parent, dict) else None
        if not isinstance(value, dict):
            self.err("malformed", f"{where}{key} must be an object")
            return None
        return value

    def arr(self, parent, key, where):
        value = parent.get(key) if isinstance(parent, dict) else None
        if not isinstance(value, list):
            self.err("malformed", f"{where}{key} must be an array")
            return None
        return value

    def text(self, parent, key, where, allow_empty=False):
        value = parent.get(key) if isinstance(parent, dict) else None
        if not isinstance(value, str) or (not value and not allow_empty):
            self.err("malformed", f"{where}{key} must be a {'string' if allow_empty else 'non-empty string'}")
            return None
        return value

    def unique(self, ids, what):
        dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
        if dupes:
            self.err("duplicate_id", f"duplicate {what}: {dupes}")
        return not dupes


def _is_number(x):
    return type(x) in (int, float) and math.isfinite(x)


def check_run(doc, side, path, file_sha256, policy: Policy):
    """Validate one run document. Returns (Run | None, issues)."""
    ctx = _Ctx(side)
    if not isinstance(doc, dict):
        ctx.err("malformed", "run document must be a JSON object")
        return None, ctx.issues
    if doc.get("schema_version") != SUPPORTED_SCHEMA:
        ctx.err("unsupported_schema", f"schema_version {doc.get('schema_version')!r} != {SUPPORTED_SCHEMA!r}")
        return None, ctx.issues
    run_meta = ctx.obj(doc, "run", "")
    run_id = None
    if run_meta is not None:
        run_id = ctx.text(run_meta, "run_id", "run.")
        if run_meta.get("status") != "completed":
            ctx.err("run_not_completed", f"run.status is {run_meta.get('status')!r}, expected 'completed'")

    config = ctx.obj(doc, "config", "")
    ks = max_k = methods = None
    if config is not None:
        ks = _check_ks(ctx, config, policy)
        max_k = config.get("max_k")
        if type(max_k) is not int or max_k < 1:
            ctx.err("malformed", "config.max_k must be a positive integer")
            max_k = None
        elif max_k < policy.k:
            ctx.err("cutoff_mismatch", f"config.max_k {max_k} is below the policy cutoff k={policy.k}")
        methods = ctx.arr(config, "methods", "config.")
        if methods is not None:
            if not all(isinstance(m, str) for m in methods) or not ctx.unique(methods, "config.methods"):
                methods = None
            elif policy.method not in methods:
                ctx.err("method_missing", f"policy method {policy.method!r} not in config.methods {methods}")
                methods = None

    dataset = _check_dataset(ctx, doc)
    policies = doc.get("policies")
    if not isinstance(policies, (dict, str, list)):
        ctx.err("malformed", "policies must be present")
    adapters = ctx.obj(doc, "adapters", "")
    adapter = ctx.obj(adapters, policy.method, "adapters.") if adapters is not None else None
    code = ctx.obj(doc, "code", "")
    if code is not None:
        fp = code.get("source_fingerprint")
        if not isinstance(fp, dict) or not isinstance(fp.get("sha256"), str):
            ctx.err("malformed", "code.source_fingerprint.sha256 must be a string")
    results = ctx.obj(doc, "results", "")
    method_block = ctx.obj(results, policy.method, "results.") if results is not None else None

    if ctx.issues or None in (ks, max_k, methods, dataset, adapter, method_block):
        return None, ctx.issues

    rows, seen = _check_rows(ctx, method_block, dataset, ks, max_k)
    if seen is not None:
        _check_row_set_and_totals(ctx, rows, seen, method_block, dataset, ks)
    if ctx.issues or rows is None:
        return None, ctx.issues
    return (
        Run(
            side=side,
            path=str(path),
            file_sha256=file_sha256,
            run_id=run_id,
            code=code,
            ks=tuple(ks),
            max_k=max_k,
            designation=dataset["designation"],
            files=tuple(sorted(dataset["files"])),
            documents=tuple(sorted(dataset["documents"].items())),
            queries=tuple(sorted(dataset["queries"].items())),
            judgments=tuple(sorted(dataset["judgments"])),
            policies=policies,
            adapter=adapter,
            results=rows,
        ),
        ctx.issues,
    )


def _check_ks(ctx, config, policy):
    ks = ctx.arr(config, "ks", "config.")
    if ks is None:
        return None
    if not ks or any(type(k) is not int or k < 1 for k in ks):
        ctx.err("malformed", "config.ks must be a non-empty array of positive integers")
        return None
    if not ctx.unique(ks, "config.ks"):
        return None
    if policy.k not in ks:
        ctx.err("cutoff_mismatch", f"policy cutoff k={policy.k} is not in config.ks {ks}")
        return None
    return ks


def _check_dataset(ctx, doc):
    ds = ctx.obj(doc, "dataset", "")
    if ds is None:
        return None
    start = len(ctx.issues)
    designation = ctx.text(ds, "designation", "dataset.")
    files = []
    raw_files = ctx.arr(ds, "files", "dataset.")
    for f in raw_files or []:
        name = f.get("name") if isinstance(f, dict) else None
        sha = f.get("sha256") if isinstance(f, dict) else None
        if not isinstance(name, str) or not name or not isinstance(sha, str) or not _SHA256.match(sha):
            ctx.err("malformed", "dataset.files entries need a name and a lowercase 64-hex sha256")
        else:
            files.append((name, sha))
    if raw_files is not None:
        if not raw_files:
            ctx.err("malformed", "dataset.files must not be empty")
        ctx.unique([n for n, _ in files], "dataset.files names")
        absent = [n for n in REQUIRED_DATASET_FILES if n not in {name for name, _ in files}]
        if absent:
            ctx.err("missing_dataset_file", f"dataset.files lacks required checksum entries: {absent}")

    documents = _id_text_map(ctx, ds, "documents", "doc_id")
    queries = _id_text_map(ctx, ds, "queries", "query_id")

    judgments = []
    raw = ctx.arr(ds, "judgments", "dataset.")
    for j in raw or []:
        if not (isinstance(j, list) and len(j) == 3 and isinstance(j[0], str) and isinstance(j[1], str)
                and type(j[2]) is int and j[2] in (0, 1)):
            ctx.err("malformed", f"dataset.judgments entry {j!r} must be [query_id, doc_id, 0|1]")
            continue
        judgments.append((j[0], j[1], j[2]))
    if raw is not None:
        ctx.unique([(q, d) for q, d, _ in judgments], "judgments (query_id, doc_id)")
        for q, d, _ in judgments:
            if queries is not None and q not in queries:
                ctx.err("unknown_id", f"judgment references unknown query {q!r}")
                break
        for q, d, _ in judgments:
            if documents is not None and d not in documents:
                ctx.err("unknown_id", f"judgment references unknown document {d!r}")
                break
        if queries is not None:
            judged_queries = {q for q, _, _ in judgments}
            unjudged = sorted(set(queries) - judged_queries)
            if unjudged:
                ctx.err("missing_judgments", f"no judgment rows for dataset queries {unjudged}; explicit 0 "
                                             "judgments are required, absence is not 'no relevant documents'")
    if len(ctx.issues) > start or None in (designation, documents, queries):
        return None
    return {"designation": designation, "files": files, "documents": documents, "queries": queries,
            "judgments": judgments}


def _id_text_map(ctx, ds, key, id_key):
    rows = ctx.arr(ds, key, "dataset.")
    if rows is None:
        return None
    ids, out = [], {}
    for r in rows:
        rid = r.get(id_key) if isinstance(r, dict) else None
        text = r.get("text") if isinstance(r, dict) else None
        if not isinstance(rid, str) or not rid or not isinstance(text, str):
            ctx.err("malformed", f"dataset.{key} entries need a non-empty {id_key} and a text string")
            continue
        ids.append(rid)
        out[rid] = text
    ctx.unique(ids, f"dataset.{key} {id_key}s")
    return out


def _check_rows(ctx, block, dataset, ks, max_k):
    """Validate every per-query row on its own (structure, ranking, labels, saved vs recomputed recall)."""
    raw_rows = ctx.arr(block, "queries", "results.<method>.")
    if raw_rows is None:
        return None, None
    qrels: dict[str, dict[str, int]] = {}
    for q, d, r in dataset["judgments"]:
        qrels.setdefault(q, {})[d] = r
    out: dict[str, QueryResult] = {}
    seen = []
    ok = True
    for row in raw_rows:
        qid = row.get("query_id") if isinstance(row, dict) else None
        if not isinstance(qid, str) or not qid:
            ctx.err("malformed", "result row without a string query_id")
            ok = False
            continue
        seen.append(qid)
        if qid not in dataset["queries"]:
            continue  # reported as extra_result by the row-set check; no judgments exist to validate it against
        parsed = _check_row(ctx, row, qid, qrels.get(qid, {}), dataset, ks, max_k)
        if parsed is None:
            ok = False
        elif qid not in out:
            out[qid] = parsed
    if not ctx.unique(seen, "result rows (query_id)"):
        ok = False
    return (out if ok else None), seen


def _check_row(ctx, row, qid, judged, dataset, ks, max_k):
    where = f"results.<method>.queries[{qid}]"
    start = len(ctx.issues)
    status = row.get("status")
    if status not in STATUSES:
        ctx.err("malformed", f"{where}.status {status!r} not in {STATUSES}")
    for key in ("n_relevant", "n_returned"):
        if type(row.get(key)) is not int or row[key] < 0:
            ctx.err("malformed", f"{where}.{key} must be a non-negative integer")
    metrics = row.get("metrics")
    saved_recall = {}
    if not isinstance(metrics, dict):
        ctx.err("malformed", f"{where}.metrics must be an object")
    else:
        for k in ks:
            m = metrics.get(str(k))
            if not isinstance(m, dict) or "recall" not in m:
                ctx.err("malformed", f"{where}.metrics[{k!r}].recall is missing (null is allowed only for "
                                     "no_judged_relevant; a missing key never is)")
            elif m["recall"] is not None and not (_is_number(m["recall"]) and 0 <= m["recall"] <= 1):
                ctx.err("malformed", f"{where}.metrics[{k!r}].recall {m['recall']!r} is not null or a number in [0, 1]")
            else:
                saved_recall[k] = m["recall"]
    ranking = row.get("ranking")
    ids = []
    if not isinstance(ranking, list):
        ctx.err("malformed", f"{where}.ranking must be an array")
    else:
        for pos, e in enumerate(ranking, 1):
            if not (isinstance(e, dict) and isinstance(e.get("doc_id"), str) and isinstance(e.get("label"), str)):
                ctx.err("invalid_ranking", f"{where}.ranking[{pos}] needs doc_id and label strings")
                continue
            if type(e.get("rank")) is not int or e["rank"] != pos:
                ctx.err("invalid_ranking", f"{where}: expected rank {pos} at position {pos}, got {e.get('rank')!r}")
            if not _is_number(e.get("score")):
                ctx.err("malformed_score", f"{where}.ranking[{pos}].score {e.get('score')!r} is not a finite number")
            ids.append(e["doc_id"])
    if len(ctx.issues) > start:
        return None

    ctx.unique(ids, f"doc_ids in ranking of {qid}")
    for d in ids:
        if d not in dataset["documents"]:
            ctx.err("invalid_ranking", f"{where} ranks unknown document {d!r}")
    if len(ids) > max_k:
        ctx.err("invalid_ranking", f"{where} has {len(ids)} entries, more than config.max_k={max_k}")
    keys = [(-e["score"], e["doc_id"]) for e in ranking]
    if keys != sorted(keys):
        ctx.err("invalid_ranking", f"{where} is not ordered by score descending then doc_id ascending")
    for e in ranking:
        want = LABELS[judged.get(e["doc_id"])]
        if e["label"] != want:
            ctx.err("invalid_ranking", f"{where}/{e['doc_id']}: label {e['label']!r} != {want!r} from judgments")
    relevant = {d for d, r in judged.items() if r == 1}
    if row["n_returned"] != len(ranking):
        ctx.err("count_mismatch", f"{where}.n_returned {row['n_returned']} != {len(ranking)} ranking entries")
    if row["n_relevant"] != len(relevant):
        ctx.err("count_mismatch", f"{where}.n_relevant {row['n_relevant']} != {len(relevant)} judged-relevant")
    want_status = "scored" if relevant else "no_judged_relevant"
    if status != want_status:
        ctx.err("status_mismatch", f"{where}.status {status!r} != {want_status!r} derived from judgments")

    recalls = {}
    for k in ks:
        if relevant:
            hits = len(relevant.intersection(ids[:k]))
            recalls[k] = Fraction(hits, len(relevant))
        else:
            recalls[k] = None
        saved = saved_recall[k]
        if recalls[k] is None:
            if saved is not None:
                ctx.err("recall_mismatch", f"{where}@{k}: saved recall {saved!r} but the query has no judged-relevant "
                                           "documents (must be null)")
        elif saved is None:
            ctx.err("recall_mismatch", f"{where}@{k}: saved recall is null but recomputed {recalls[k]} "
                                       "(null is only legitimate for no_judged_relevant)")
        elif abs(saved - float(recalls[k])) > FLOAT_TOLERANCE:
            ctx.err("recall_mismatch", f"{where}@{k}: saved recall {saved!r} != recomputed {recalls[k]} "
                                       f"({float(recalls[k])!r})")
    if len(ctx.issues) > start:
        return None
    return QueryResult(qid, status, row["n_relevant"], row["n_returned"], tuple(ids), recalls)


def _check_row_set_and_totals(ctx, rows, seen, block, dataset, ks):
    expected = set(dataset["queries"])
    got = set(seen)
    for qid in sorted(expected - got):
        ctx.err("missing_result", f"no result row for dataset query {qid!r}")
    for qid in sorted(got - expected):
        ctx.err("extra_result", f"result row for query {qid!r} that is not in dataset.queries")
    if got != expected or rows is None:
        return  # saved counts/aggregate cannot be checked against an incomplete, inflated or invalid set
    scored = [r for r in rows.values() if r.status == "scored"]
    counts = block.get("counts")
    want = {"total": len(rows), "scored": len(scored), "no_judged_relevant": len(rows) - len(scored)}
    if counts != want:
        ctx.err("count_mismatch", f"results.<method>.counts {counts!r} != recomputed {want!r}")
    if not scored:
        ctx.err("empty_scored_set", "no query has judged-relevant documents; nothing can be scored")
        return
    aggregate = block.get("aggregate")
    for k in ks:
        mean = sum(r.recalls[k] for r in scored) / len(scored)
        saved = aggregate.get(str(k), {}).get("macro_recall") if isinstance(aggregate, dict) else None
        if saved is None or not _is_number(saved) or abs(saved - float(mean)) > FLOAT_TOLERANCE:
            ctx.err("aggregate_mismatch", f"results.<method>.aggregate[{k!r}].macro_recall {saved!r} != "
                                          f"recomputed {mean} ({float(mean)!r})")


# --------------------------------------------------------------------------- pair checks
def _canon(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def _show(items, limit=5):
    items = list(items)
    return str(items[:limit]) + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def check_pair(base: Run, cand: Run, declared: dict):
    """Cross-run compatibility. Returns (issues, declared_change_records)."""
    issues: list[Issue] = []

    def err(code, msg):
        issues.append(Issue(code, "pair", msg))

    if base.run_id == cand.run_id:
        err("same_run", f"baseline and candidate share run_id {base.run_id!r}; a run cannot be compared with itself")
    if base.designation != cand.designation:
        err("dataset_mismatch", f"dataset.designation {base.designation!r} != {cand.designation!r}")
    if base.files != cand.files:
        diff = sorted(set(base.files) ^ set(cand.files))
        err("dataset_mismatch", f"dataset.files (name, sha256) differ: {_show(diff)}")
    for label, a, b in (("documents", base.documents, cand.documents), ("queries", base.queries, cand.queries),
                        ("judgments", base.judgments, cand.judgments)):
        if a != b:
            err("dataset_mismatch", f"embedded dataset.{label} differ: {_show(sorted(set(a) ^ set(b)))}")
    if base.ks != cand.ks:
        err("cutoff_mismatch", f"config.ks {list(base.ks)} != {list(cand.ks)}")
    if base.max_k != cand.max_k:
        err("cutoff_mismatch", f"config.max_k (retrieval depth) {base.max_k} != {cand.max_k}")
    if _canon(base.policies) != _canon(cand.policies):
        err("policy_mismatch", "run `policies` blocks (quality policies) differ")

    records = []
    changed = sorted(k for k in set(base.adapter) | set(cand.adapter)
                     if _canon(base.adapter.get(k, "<absent>")) != _canon(cand.adapter.get(k, "<absent>")))
    for field in changed:
        b, c = base.adapter.get(field), cand.adapter.get(field)
        if field in declared:
            records.append({"field": f"adapters.<method>.{field}", "baseline": b, "candidate": c,
                            "reason": declared[field]})
        else:
            err("undeclared_setting_change", f"adapters.<method>.{field} changed {b!r} -> {c!r} without "
                                             f"--declare-change {field}=REASON")
    for field in sorted(set(declared) - set(changed)):
        err("declared_change_not_observed", f"--declare-change {field} given but adapters.<method>.{field} is "
                                            "identical in both runs")
    return issues, records
